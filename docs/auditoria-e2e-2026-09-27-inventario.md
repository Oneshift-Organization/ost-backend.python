# Auditoria E2E — inventário, topologia e Terraform (27/09/2026)

Etapa I9 do [`PLANO_INVENTARIO_API.md`](../PLANO_INVENTARIO_API.md). Os quatro
pares de mocks (billing e inventário das mesmas contas) passaram pela **API
HTTP** de ponta a ponta: upload do billing, upload do inventário, análise com
`inventory_id`, geração de Terraform para as outras três nuvens e `terraform
validate` feito pela própria API.

A análise rodou com `LLM_PROVIDER=mock`. O que se audita aqui é o backend: o
custo, a economia, a recomendação e a ordem de migração são impostos por ele
sobre a resposta do modelo, então o texto do modelo não muda nenhum número.

Reproduzir (API com `INVENTORY_ENABLED=true` e o Terraform no PATH):

```bash
PYTHONPATH=. python scripts/gerar_mocks_billing.py --destino /tmp/mocks
PYTHONPATH=. python scripts/gerar_mocks_inventario.py --billing /tmp/mocks --destino /tmp/inv
python scripts/e2e_inventario.py --url http://localhost:8000 --mocks /tmp/mocks --inventarios /tmp/inv
```

O script sai com código 1 se qualquer checagem de coerência falhar.

## Resultado

| origem | ondas | cobertura do billing | recomendado | destino | VMs no `.tf` / aprovadas | subredes | grupos | `validate` |
|---|---|---|---|---|---|---|---|---|
| AWS | 3 | 82,1% | OCI | GCP | 116 / 116 | 9 | 39 firewalls | ✅ |
| | | | | Azure | 125 / 125 | 9 | 20 ASGs | ✅ |
| | | | | OCI | 110 / 110 | 9 | 31 NSGs | ✅ |
| GCP | 2 | 100% | OCI | AWS | 9 / 9 | 9 | 13 | ✅ |
| | | | | Azure | 7 / 7 | 7 | 11 | ✅ |
| | | | | OCI | 6 / 6 | 6 | 8 | ✅ |
| Azure | 2 | 100% | OCI | AWS | 34 / 34 | 6 | 19 | ✅ |
| | | | | GCP | 30 / 30 | 5 | 6 | ✅ |
| | | | | OCI | 30 / 30 | 5 | 18 | ✅ |
| OCI | 3 | 100% | manter | AWS | 25 / 25 | 3 | 6 | ✅ |
| | | | | GCP | 23 / 23 | 3 | 9 | ✅ |
| | | | | Azure | 23 / 23 | 3 | 6 | ✅ |

"Subredes" conta a subrede padrão; as demais são espelhadas da origem.

## Coerência entre os três artefatos, verificada

- **O mesmo inventário do começo ao fim.** O fingerprint do inventário, o da
  topologia que a análise congelou e o do `manifest.json` de cada pacote são o
  mesmo nos 12 pares. Reenviar o mesmo conteúdo dá o mesmo fingerprint (a AWS
  saiu com `b742d59e…` nas duas execuções).
- **A ordem de migração é do backend.** A sequência da resposta abre com "Onda
  0 — fundação" e segue as ondas calculadas, não o texto do modelo.
- **O posicionamento muda onde as VMs ficam, nunca quantas.** Em todo par, as
  VMs do `.tf` somam exatamente o que a análise aprovou para o destino.
- **Risco alto não migra.** Nenhum pacote libera administração (22, 3389, 5985,
  5986) ou banco (5432, 3306, 1433, 1521, 6379, 27017) para a internet, em
  nenhuma das quatro formas (regra da AWS, firewall do GCP, NSG da Azure, regra
  de NSG da OCI). Na origem da AWS havia 45 recursos com administração exposta
  e 24 com banco exposto.
- **Recomendação.** O inventário não muda custo nem recomendação (teste de
  neutralidade). Na OCI, os três destinos saem de US$ 4,2 mil a 6 mil por mês
  mais caros, e o resultado é manter.

## Números que conferem com o gerador dos mocks

- **Cobertura (AWS).** US$ 58.215 de US$ 70.892 com recurso: 82,1%. A lacuna é a
  conta `333333333333`, que ficou fora do snapshot do Config (US$ 12.265), e os
  snapshots de EBS, que o Config não registra.
- **Discos (AWS).** 48.626 GB medidos em 99 volumes. 36.334 GB de boot viram o
  disco raiz das VMs, 3.479 GB sem anexo não são gerados (ociosos), e sobram 18
  volumes avulsos de 490 GB.
- **Discos (GCP).** Um disco de boot por VM (26). Os 21 discos sem anexo são dos
  projetos que pagam PD sem pagar compute na região, o que vem do CSV real.
- **Posicionamento (AWS → GCP).** O pool C7G tem 19 VMs aprovadas em 7 subredes
  (4, 3, 3, 2, 2, 2 e 3 na padrão, pelas 13 instâncias fora do inventário).
- **Grupos de segurança.** O SSH do bastion vira a faixa dele na origem
  (`10.10.0.0/20`), que continua lá e passa pela VPN. No GCP, as regras de SSH
  pelo IAP do Google (`35.235.240.0/20`) não são levadas a outra nuvem. Na OCI,
  11 VMs herdam mais de 5 grupos e recebem um grupo combinado.
- **Banco.** O Flexible Server da Azure (billing sem classe) sai com a classe do
  inventário (`Standard_D4ds_v5`, PostgreSQL 14 como default da versão) e, na
  AWS, num grupo de subredes da VPC nova, completado com uma subrede de outra
  zona.

## O que as revisões de etapa corrigiram

Achados ao ler o payload e o pacote, cada um com teste:

| achado | onde | correção |
|---|---|---|
| Balanced PD (94% do disco do GCP) classificado como HDD; SSD de referência do GCP era o pd-ssd | billing | `fix/gcp-balanced-pd`: regra nova, pd-balanced como referência, faixa de tamanho de disco por destino |
| Mock do GCP com discos na zona errada, disco com dois SKUs e tamanho ×10 | mocks | disco na zona de uma VM do projeto; id com o SKU; tamanho certo |
| Posicionamento partido em dezenas de linhas (C7G) | análise (I7) | uma linha por subrede, grupos unidos |
| "A versão não aparece no billing: preencha" com a versão vinda do inventário | Terraform (I8a) | o texto diz de onde veio a versão |
| Contagem de riscos do README tirada da lista resumida | Terraform (I8a) | contagens do resumo inteiro |
| RDS sem grupo de subredes caía na VPC padrão da conta | Terraform (I8c) | grupo completado com subrede de outra zona |
| VM pedia IP público pela subrede de origem | Terraform (I8c) | vale a subrede de destino |
| Aviso de subrede sem máquina ambíguo (mesmo nome em projetos diferentes) | Terraform (I8c) | sai com o CIDR |

## Limitações conhecidas

- **Mocks.** Os bills de Azure e OCI não cobram disco de SO, então o pacote
  nessas origens não tem boot para o disco raiz (os normalizadores tratam o
  caso, com teste). No mock da AWS todo volume tem ~500 GB, e o boot também.
- **Dado real.** Nada disto foi rodado sobre inventário de cliente. Os formatos
  foram conferidos contra a documentação oficial de cada provedor (§2 do plano).
- **Fora desta entrega.** O empacotador pode expandir pool pequeno (2 instâncias
  FSV2 viram 9 VMs na Azure → AWS) sem aviso. Filestore e o SKU "Storage" do
  Source Repositories ainda caem em `storage_hdd`. Na Azure, a regra padrão
  `AllowVnetInBound` libera o tráfego entre as subredes da VNet (há aviso no
  pacote). Banco fora da AWS não é posto nas subredes espelhadas (exige endpoint
  privado, decisão de rede do cliente).
