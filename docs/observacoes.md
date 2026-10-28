# Principais observações

Achados de cada ferramenta da [esteira](pipeline.md), como foram classificados e o que foi feito. Os links de execução e de commit são a evidência.

Execuções citadas:

| Execução | O que mostra |
|---|---|
| [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552) | Primeira execução completa, etapas 1 a 7 verdes |
| [40695444458](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695444458) | Execução **bloqueada** pelo Gitleaks (segredo fictício) |
| [40695948375](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695948375) | Mesma branch depois da correção, etapas 1 a 7 verdes |
| [40695605119](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695605119) | Gitleaks acusando a chave de outra branch (ver G3) |
| [40696974253](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40696974253) | Gitleaks restrito ao histórico do commit |

Commits citados: [`2b4f132`](https://github.com/Oneshift-Organization/ost-backend.python/commit/2b4f132) (correções para os gates), [`d73a2ce`](https://github.com/Oneshift-Organization/ost-backend.python/commit/d73a2ce) (correção do segredo), [`4e45e93`](https://github.com/Oneshift-Organization/ost-backend.python/commit/4e45e93) (escopo do Gitleaks).

## Gitleaks

| # | Achado | Onde | Classificação | Ação tomada | Resultado |
|---|---|---|---|---|---|
| G1 | `generic-api-key` | `scripts/sync_prices.py:55`, commits `e2047cf` e `d3cf694` | Falso positivo | A regra casou com o nome do atributo `gcp_billing_api_key`, lido da configuração; não há valor de chave. Fingerprints no [`.gitleaksignore`](../.gitleaksignore) | 0 segredos na [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552) |
| G2 | `aws-access-token` | `scripts/demo_credencial.py:6`, branch `test/gitleaks-bloqueio` | Vulnerabilidade real (simulada com chave fictícia gerada aleatoriamente) | Histórico da branch reescrito sem o commit da chave; a credencial passou a vir do ambiente em [`d73a2ce`](https://github.com/Oneshift-Organization/ost-backend.python/commit/d73a2ce) | Bloqueio na [40695444458](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695444458), verde na [40695948375](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695948375) |
| G3 | `aws-access-token` (o mesmo de G2) | Esteira da branch `feat/devsecops-pipeline` | Falha de configuração da esteira | Com `fetch-depth: 0` o checkout traz todas as branches, e o Gitleaks varre `--all` por padrão. Escopo limitado com `--log-opts="HEAD"` em [`4e45e93`](https://github.com/Oneshift-Organization/ost-backend.python/commit/4e45e93) | Falha na [40695605119](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695605119), verde na [40696974253](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40696974253) |

Sobre G2: apagar o arquivo num commit novo não resolveria, porque a chave continua no histórico. Com um segredo real, o primeiro passo seria **rotacionar a credencial**; reescrever o histórico vem depois, e não basta sozinho, porque o commit antigo continua acessível pelo SHA e por forks.

## Semgrep

Varredura inicial com `p/default` e `p/owasp-top-ten`: 21 achados.

| # | Achado | Onde | Classificação | Ação tomada | Resultado |
|---|---|---|---|---|---|
| S1 | `dockerfile.security.missing-user` (ERROR) | `Dockerfile:81` | Vulnerabilidade real | API passou a rodar com `USER 10001` ([`2b4f132`](https://github.com/Oneshift-Organization/ost-backend.python/commit/2b4f132)) | 0 achados na [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552) |
| S2 | `github-actions-mutable-action-tag` (11x) | `tests.yml`, `build-push.yml` | Vulnerabilidade real (cadeia de suprimentos) | Actions fixadas pelo SHA do commit | Idem |
| S3 | [`dynamic-urllib-use-detected`](https://semgrep.dev/r/python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected) (7x) | Clientes de preço (`app/infrastructure/pricing/*_price_client.py`), câmbio (`fx/ecb_fx_client.py`) e 2 scripts | Falso positivo | Os clientes chamam hosts fixos das APIs oficiais (AWS, Azure, GCP, OCI, Frankfurter); os scripts validam o esquema `http(s)` antes. `nosemgrep` com a regra na linha | Idem |
| S4 | `len-all-count` (2x) | `app/infrastructure/database/seed_mock_catalog.py:155,162` | Vulnerabilidade real (desempenho: carrega a tabela para contar) | Trocado por `select(func.count())` | Idem |

## Build e dependências (pip-audit)

| # | Achado | Onde | Classificação | Ação tomada | Resultado |
|---|---|---|---|---|---|
| B1 | Dependências sem versão fixa | `requirements.txt` só tinha faixas (`>=`) | Vulnerabilidade real (build não reprodutível; o Trivy não analisa sem lock) | Faixas movidas para `requirements.in`; `requirements.txt` gerado com [`uv pip compile`](https://docs.astral.sh/uv/pip/compile/), 84 pacotes com `==` | pip-audit com 0 vulnerabilidades na [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552) |

## Trivy fs (etapa 4)

| # | Achado | Onde | Classificação | Ação tomada | Resultado |
|---|---|---|---|---|---|
| T1 | [CVE-2024-47874](https://nvd.nist.gov/vuln/detail/CVE-2024-47874), [CVE-2026-48818](https://nvd.nist.gov/vuln/detail/CVE-2026-48818), [CVE-2026-54283](https://nvd.nist.gov/vuln/detail/CVE-2026-54283) (HIGH) | `starlette` 0.37.2 | Vulnerabilidade real | A faixa `fastapi<0.112` prendia o Starlette. FastAPI subiu para 0.162.0 e Starlette para 1.10.0 (`starlette>=1.3.1` no `requirements.in`). Testes unitários e de integração passaram | 0 HIGH/CRITICAL na [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552) |
| T2 | `DS-0002` Image user should not be 'root' (HIGH) | `Dockerfile` | Vulnerabilidade real (o mesmo de S1) | `USER 10001` | Idem |

## Trivy image (etapa 6)

Antes de publicar, escaneamos as imagens que entram no build.

| # | Achado | Onde | Classificação | Ação tomada | Resultado |
|---|---|---|---|---|---|
| I1 | Mais de 30 CVEs HIGH/CRITICAL com correção (Go `stdlib`, `grpc`, `x/crypto`, `x/net`, `go-getter` e outros) | Binário `/usr/local/bin/terraform` 1.9.8, copiado do estágio `hashicorp/terraform` | Vulnerabilidade real | Terraform subiu para 1.17.0 no Dockerfile e no CI | 0 HIGH/CRITICAL na [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552) |
| I2 | [CVE-2026-116587](https://nvd.nist.gov/vuln/detail/CVE-2026-116587) (HIGH) | `go-jose/v4` dentro do Terraform 1.17.0 | Risco aceito | A correção ainda não saiu num release do Terraform. A API só roda `terraform fmt` e `validate` offline, sem backend remoto nem autenticação JOSE/JWT: o código vulnerável não é alcançável. No [`.trivyignore`](../.trivyignore), para rever a cada release | Idem |
| I3 | Nenhum HIGH/CRITICAL com correção | `python:3.12-slim` | - | Imagem base mantida | Idem |

## OWASP ZAP (etapa 7)

| # | Achado | Onde | Classificação | Ação tomada | Resultado |
|---|---|---|---|---|---|
| Z1 | [10049 Storable and Cacheable Content](https://www.zaproxy.org/docs/alerts/10049/) (Informational), 3 instâncias | `/`, `/robots.txt` | Risco aceito | São respostas públicas, sem dado de usuário. As rotas com dados exigem token `Bearer`, e respostas a requisições com `Authorization` não são armazenadas por caches compartilhados ([RFC 9111, seção 3.5](https://www.rfc-editor.org/rfc/rfc9111#section-3.5)) | Nenhum alerta High ou Medium na [40695948375](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40695948375) |

O baseline faz spider de links HTML e a API devolve JSON, então a cobertura é rasa: ele vê pouco além da raiz. Ver a lição 5.

## Lições aprendidas

1. **O Trivy foi a ferramenta com mais achados reais.** A faixa `fastapi<0.112` escondia 3 CVEs HIGH no Starlette, e sem versões fixas o SCA nem conseguiria enxergá-los. Lock file é pré-requisito, não detalhe.
2. **O Semgrep foi a ferramenta com mais falsos positivos.** As 7 chamadas `urllib` usam hosts fixos. Mesmo assim cada supressão ficou na linha, com a regra nomeada, para não virar um "deixar verde" em massa.
3. **O risco maior da imagem não estava na imagem base.** O `python:3.12-slim` estava limpo; os mais de 30 CVEs vinham de um binário copiado para dentro dela. Só o scan da imagem publicada (etapa 6) pegou isso.
4. **A própria esteira tem cadeia de suprimentos.** A `trivy-action` teve as tags sequestradas em março de 2026 ([GHSA-69fq-xp46-6x23](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)). Por isso todas as actions estão fixadas pelo SHA. E uma configuração errada da ferramenta (G3) pode bloquear a esteira inteira por um achado de outra branch.
5. **O ZAP Baseline cobre pouco numa API.** Ele só segue links. A próxima melhoria é o [API scan](https://www.zaproxy.org/docs/docker/api-scan/) do ZAP alimentado pelo OpenAPI da aplicação, que testa cada rota declarada.
6. **Tempo.** A primeira execução completa levou cerca de 7 minutos e meio (de 12:57 a 13:05 UTC na [40692183552](https://github.com/Oneshift-Organization/ost-backend.python/actions/runs/40692183552)). Semgrep, build e ZAP somam a maior parte. Com os testes de integração dentro da etapa 3, ela ficou mais lenta, mas o projeto passou a ter um workflow só em vez de dois rodando os mesmos testes.
7. **O que melhoraríamos:** assinar a imagem com Cosign e gerar SBOM, ZAP API scan, e fazer o `build-push.yml` da `develop` esperar a esteira em vez de publicar em paralelo.
