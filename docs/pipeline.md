# Esteira DevSecOps

Workflow: [`.github/workflows/devsecops.yml`](../.github/workflows/devsecops.yml). Execuções: [Actions → DevSecOps](https://github.com/Oneshift-Organization/ost-backend.python/actions/workflows/devsecops.yml).

## Quando roda

| Evento | Branches | Etapas |
|---|---|---|
| `pull_request` | para `develop` ou `main` | 1 a 7 e resumo |
| `push` | `main` | 1 a 8 e resumo (a 8 espera aprovação) |
| `workflow_dispatch` | qualquer | 1 a 7 e resumo |

Na `develop` a esteira roda só nos PRs: o push do merge repetiria o mesmo código e disputaria os runners. As etapas são sequenciais (`needs`): uma falha interrompe as seguintes. A exceção são os testes de integração (3b), que rodam em paralelo para não atrasar as etapas 3 a 7. Um push novo na mesma branch cancela a execução anterior ([concurrency](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs)), exceto na `main`, onde um deploy pode estar aguardando aprovação.

## Permissões

O workflow declara `contents: read` no topo. Cada job só ganha o que precisa ([hardening do GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)):

| Job | Permissão extra | Para quê |
|---|---|---|
| `sast` | `security-events: write` | Enviar o SARIF para a aba Security |
| `docker-build` | `packages: write`, `id-token: write` | Publicar a imagem no GHCR e assiná-la com Cosign |
| `image-scan` | `packages: write`, `id-token: write` | Baixar a imagem e publicar o atestado do SBOM |
| `dast-staging` | `packages: read` | Baixar a imagem |
| `deploy-prod` | `packages: write`, `actions: read` | Criar a tag de produção e ler quem aprovou |

Todas as actions estão fixadas pelo SHA do commit, com a versão em comentário. Em março de 2026 as tags da `aquasecurity/trivy-action` foram sequestradas ([GHSA-69fq-xp46-6x23](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)); uma tag é mutável, o SHA não.

## Etapas

### 1. Secrets: Gitleaks

[Gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 sobre o histórico do commit em análise (`fetch-depth: 0` e `--log-opts="HEAD"`), com `--redact` para o relatório não reexpor o segredo. O binário é baixado do release oficial e conferido pelo SHA-256; a `gitleaks-action` exige licença paga para organizações. Falsos positivos ficam no [`.gitleaksignore`](../.gitleaksignore).

Gate: `--exit-code 1` com qualquer segredo. Relatório: `relatorio-01-gitleaks/gitleaks.json`.

### 2. SAST: Semgrep

[Semgrep](https://semgrep.dev/docs/) 1.182.0 com os rulesets `p/default` e `p/owasp-top-ten` ([OWASP Top 10](https://owasp.org/Top10/)). Gera JSON para o relatório e SARIF para a aba Security ([upload de SARIF](https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/uploading-a-sarif-file-to-github)). Supressões só com `nosemgrep: <regra>` na linha e justificativa ([ignorar código](https://semgrep.dev/docs/ignoring-files-folders-code)).

Gate: `--error` com qualquer achado. Relatório: `relatorio-02-semgrep/semgrep.json`.

### 3. Build, dependências e artefato

O PDF usa Node.js como exemplo; a adaptação para Python:

| Node.js | Aqui |
|---|---|
| `npm ci` | `pip install -r requirements-dev.txt`, com versões fixas em `requirements.txt` |
| `package-lock.json` | `requirements.txt` gerado de `requirements.in` com [`uv pip compile`](https://docs.astral.sh/uv/pip/compile/) |
| `npm ls` | `pipdeptree` |
| `npm audit --json` | [`pip-audit`](https://github.com/pypa/pip-audit) `-f json` |
| `npm test` | `ruff` + `pytest` unitário + `pytest -m integration` |

Os testes de integração ficam no job **3b**, que começa junto com a etapa 3 e roda em paralelo com as etapas 4 a 7: é o passo mais lento (cerca de 4 minutos). Rodam contra um Postgres descartável (service container), depois das migrations e dos seeds de referência, e um teste pulado é tratado como falha: significa banco ou seed ausente. O 3b continua bloqueando: a etapa 8 só roda se ele passar. O artefato é `ost-api-<sha>.tar.gz` (`git archive` do commit).

Gate: falha no lint das áreas já limpas ou em qualquer teste (unitário na etapa 3, integração no 3b). A auditoria é informativa; o bloqueio de dependências é na etapa 4. Relatório: `relatorio-03-build` (`arvore-dependencias.txt`, `pip-audit.json`) e artifact `artefato-aplicacao`.

### 4. SCA: Trivy fs

[Trivy](https://trivy.dev/latest/docs/) 0.76.1 sobre o repositório com os scanners `vuln`, `misconfig` e `secret`. Gera tabela completa e JSON só com HIGH/CRITICAL. Exceções no [`.trivyignore`](../.trivyignore) ([filtragem](https://trivy.dev/latest/docs/configuration/filtering/)).

Gate: `--exit-code 1` com HIGH ou CRITICAL. Relatório: `relatorio-04-trivy-sca/trivy-fs.{txt,json}`.

### 5. Imagem no GHCR

Docker Buildx publica `ghcr.io/oneshift-organization/ost-backend.python/ost-api:<sha>` no [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), autenticado com o `GITHUB_TOKEN`. O [Dockerfile](../Dockerfile) é [multi-stage](https://docs.docker.com/build/building/multi-stage/) sobre `python:3.12-slim`, sem segredo em `ENV`/`ARG`, e roda como o usuário `10001`. Não há tag `latest`: produção referencia a imagem pelo digest.

Depois do push, a imagem é assinada pelo digest com [Cosign](https://docs.sigstore.dev/cosign/signing/signing_with_containers/) no modo keyless: o certificado vem do [OIDC do GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-openid-connect), não há chave guardada, e a assinatura fica no log de transparência público do Sigstore.

Gate: falha no build, no push ou na assinatura. Relatório: `relatorio-05-docker-build/docker-build.md` (imagem, digest, identidade da assinatura e manifesto).

### 6. Scan da imagem: Trivy image

Trivy sobre a imagem publicada na etapa 5. As credenciais do registry vão por variável de ambiente (`TRIVY_USERNAME`, `TRIVY_PASSWORD`), nunca na linha de comando.

A etapa também gera o [SBOM da imagem](https://trivy.dev/latest/docs/supply-chain/sbom/) em [CycloneDX](https://cyclonedx.org/specification/overview/). Depois do gate, o SBOM é publicado como [atestado assinado](https://docs.sigstore.dev/cosign/verifying/attestation/) ao lado da imagem (`cosign attest --type cyclonedx`): só imagens aprovadas no scan recebem o atestado.

Gate: `--exit-code 1` com HIGH ou CRITICAL **que já tenham correção** (`--ignore-unfixed`). Relatório: `relatorio-06-trivy-image/` (`trivy-image.{txt,json}` e `sbom.cdx.json`).

**Etapa 4 x etapa 6.** A etapa 4 enxerga o que está no repositório: as bibliotecas Python declaradas e o Dockerfile. A etapa 6 enxerga o que de fato vai para produção: os pacotes do Debian da imagem base e tudo o que foi copiado para dentro dela. Aqui a diferença apareceu na prática: o repositório estava limpo depois da etapa 4, mas a imagem levava o binário do Terraform 1.9.8, com mais de 30 CVEs HIGH/CRITICAL nas bibliotecas Go, invisíveis para o scan do repositório. Detalhes em [observacoes.md](observacoes.md).

### 7. Homologação e DAST: OWASP ZAP

Job no environment `staging`. A imagem aprovada (pelo digest) sobe no próprio runner, junto com um Postgres descartável, depois do `alembic upgrade head`. Com `/v1/health` respondendo, o [ZAP Baseline](https://www.zaproxy.org/docs/docker/baseline-scan/) ([action](https://github.com/zaproxy/action-baseline)) testa a API de fora para dentro. O alvo é sempre o próprio runner: nenhum sistema de terceiros é varrido.

Gate: informativo (`fail_action: false`); a decisão de seguir para produção é de quem aprova. Relatório: `relatorio-07-homologacao-zap` (`report_html.html`, `report_json.json`, `report_md.md`).

### 8. Aprovação e produção

Job no environment `production`, que tem revisor obrigatório e só aceita a branch `main` ([environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)). O job fica parado até a aprovação; uma rejeição interrompe o deploy.

Antes de promover, o job [verifica](https://docs.sigstore.dev/cosign/verifying/verify/) a assinatura e o atestado do SBOM, exigindo que tenham sido emitidos por este workflow na `main` (`--certificate-identity`) pelo OIDC do GitHub (`--certificate-oidc-issuer`). Uma imagem enviada ao GHCR por fora da esteira não passa.

O deploy não reconstrói a imagem: cria a tag `main-<timestamp>-<sha>` no **mesmo digest** testado nas etapas 5 a 7. O Flux do cluster OKE acompanha essas tags ([ImagePolicy](https://fluxcd.io/flux/components/image/imagepolicies/), em `ost-iac.terraform/fluxcd/production/api/image-policy.yaml`) e atualiza o deployment. Assim a esteira não guarda credencial de nuvem nenhuma.

O relatório registra imagem, digest, commit, data, link da execução e quem aprovou, lido da [API de aprovações](https://docs.github.com/en/rest/actions/workflow-runs#get-the-review-history-for-a-workflow-run). Relatório: `relatorio-08-producao/deploy-producao.md`.

### Resumo da Esteira

Job `pipeline-summary` com `if: always()`: roda mesmo quando uma etapa falha. Baixa os relatórios, conta os achados com `jq` e publica a tabela no Summary da execução e no artifact `resumo-da-esteira`.

Como depende da etapa 8, na `main` o resumo só sai depois da aprovação ou da rejeição. Optamos por não criar um resumo parcial: quem aprova já tem os relatórios das etapas 1 a 7 nos artifacts, e o resumo da execução em PR, com as mesmas etapas, sai sem esperar.

## Como aprovar um deploy

1. Abrir a execução da `main` em Actions: o job **8. Aprovação + produção** aparece como *Waiting*.
2. Conferir os artifacts `relatorio-01` a `relatorio-07`, principalmente o ZAP.
3. **Review deployments** → marcar `production` → comentar a base da decisão → **Approve and deploy**.
4. Registrar a decisão na tabela de aprovações do [README](../README.md#aprovação-em-produção).
