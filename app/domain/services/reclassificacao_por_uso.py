"""Linha classificada errado corrigida pelo uso, na agregação (lote v4, V4.3).

A classificação vem do enriquecimento (regras de SKU, catálogo). Alguns casos saíam
errados de um jeito que mudava o número:

- **reserva de RDS e ElastiCache** (`HeavyUsage:db.*`, `HeavyUsage:cache.*`): parte
  caía em compute (`core`), virava compromisso e não era recotada; ela paga banco e
  cache, então vai para a categoria de banco, como as outras reservas de banco;
- **MSK** (`Kafka.*`): broker e disco saíam como banco relacional e eram recotados
  pela taxa de banco. Não há taxa de streaming: fica ao custo atual;
- **Source Repository**, SKU "Storage": saía como disco HDD; é armazenamento de
  objetos, como o do Artifact Registry.

PLANO_PRECO_NAO_COMPUTE R4:
- **Cloud Run e afins** (CPU, memória, instância mínima, requisição): saíam como rede
  ou não classificados. São computação serverless (`categoria_por_uso`); o tráfego
  deles continua rede;
- **segredos e chaves** (Secrets Manager, KMS, Secret Manager, Key Vault, Vault):
  saíam órfãos ou como disco HDD (requisição, "Secret version replica storage");
- **armazenamento de arquivos** (Filestore, EFS, Azure Files, File Storage da OCI):
  saía como disco HDD ou objeto, e o Terraform fazia disco dele (T4 da sprint);
- **requisição da CloudFront e consulta ao cache do Cloud CDN**: saíam órfãs. São CDN,
  no balde de rede. O nome do serviço separa a da CloudFront da requisição do S3, que
  tem o mesmo jeito de UsageType.

PLANO_EQUIVALENTES_E_TARIFAS §3:
- **logs** (CloudWatch Logs, Log Analytics, Cloud Logging, Logging da OCI): saíam não
  classificados ou como egress (o log entregue da CloudWatch);
- **DNS** (Route 53, Azure DNS, Cloud DNS, DNS da OCI): saíam não classificados ou como
  rede (a consulta da Azure);
- **WAF** (AWS WAF, política e regra do WAF do Front Door, Cloud Armor, WAF da OCI);
- **registro de imagens** (armazenamento do ECR, do Artifact Registry e do ACR): o do
  Artifact Registry era cotado como objeto, a um quarto do preço do registro;
- **métricas** (CloudWatch fora dos logs, Prometheus gerenciado, Cloud Monitoring,
  Monitoring da OCI).

PLANO_PRECO_NAO_COMPUTE R5:
- **bastion** (Azure Bastion): saía não classificado. A saída de dados dele é rede;
- **postura de segurança** (Defender for Cloud, GuardDuty, Inspector, Security Hub,
  Security Command Center, verificação de imagem do GCP, Cloud Guard): saía não
  classificada.

Corrigir aqui, e não só nas regras de SKU, vale para faturas já processadas sem
reenriquecer. Tabela de dados por nuvem, sem ``if``. Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Sem cotação: o agregador manda para o balde não classificado, ao custo atual
SEM_COTACAO = "unclassified"
# Baldes separados pelo uso (`categoria_por_uso`) e o de rede (`rede_por_uso`)
SERVERLESS = "serverless"
SEGREDOS = "secrets"
REDE = "network_egress"
ARQUIVOS = "file_storage"
LOGS = "logging"
DNS = "dns"
WAF = "waf"
REGISTRO = "container_registry"
METRICAS = "metrics"
BASTION = "bastion"
SEGURANCA = "security_posture"

# Uso de log da CloudWatch; o resto do serviço é métrica, alarme e painel
_LOG_DA_CLOUDWATCH = r"(dataprocessing|vendedlog|timedstorage|datascanned|logs?\b)"


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(texto, re.IGNORECASE)


# Provedor → (padrão em "serviço | uso", tipo de componente novo); o primeiro que casa
REGRAS: Mapping[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "aws": (
        (_r(r"heavyusage:(db|cache)\."), "database"),
        (_r(r"(kafka|msk).*\|.*kafka\."), SEM_COTACAO),
        (_r(r"cloudfront[^|]*\|.*requests"), REDE),
        (_r(r"^aws ?lambda[^|]*\|(?!.*(datatransfer|-bytes))"), SERVERLESS),
        (_r(r"^(aws ?secrets ?manager|aws ?key ?management|awskms)"), SEGREDOS),
        (_r(r"^(amazon ?efs|amazon elastic file system)"), ARQUIVOS),
        (_r(r"^amazon ?cloudwatch[^|]*\|.*" + _LOG_DA_CLOUDWATCH), LOGS),
        (_r(r"^amazon ?cloudwatch"), METRICAS),
        (_r(r"^(amazon ?prometheus|amazon managed service for prometheus)"), METRICAS),
        (_r(r"^amazon ?route ?53"), DNS),
        (_r(r"^(aws ?waf|awswaf)"), WAF),
        (_r(r"^(amazon ?ecr|amazon elastic container registry)[^|]*\|.*timedstorage"), REGISTRO),
        (_r(r"^(amazon ?guardduty|amazon ?inspector|aws ?security ?hub)"), SEGURANCA),
    ),
    "gcp": (
        (_r(r"source repositor[^|]*\| *storage$"), "object_storage"),
        (_r(r"^cloud run[^|]*\|(?!.*(data transfer|egress))"), SERVERLESS),
        (_r(r"^(secret manager|cloud key management)"), SEGREDOS),
        (_r(r"\|.*cdn.*lookup"), REDE),
        (_r(r"filestore"), ARQUIVOS),
        (_r(r"^cloud logging"), LOGS),
        (_r(r"^cloud monitoring"), METRICAS),
        (_r(r"^cloud dns"), DNS),
        (_r(r"\|.*cloud armor"), WAF),
        (_r(r"^artifact registry[^|]*\|.*storage"), REGISTRO),
        (
            _r(r"^(security command center|container registry vulnerability|on-demand scanning)"),
            SEGURANCA,
        ),
    ),
    "azure": (
        (
            _r(r"^(functions|azure container apps)[^|]*\|(?!.*(data transfer|bandwidth))"),
            SERVERLESS,
        ),
        (_r(r"^key vault"), SEGREDOS),
        (_r(r"^(storage|azure files)[^|]*\|.*\bfiles?\b"), ARQUIVOS),
        (_r(r"^log analytics|^azure monitor[^|]*\|.*\blogs\b"), LOGS),
        (_r(r"^azure monitor"), METRICAS),
        (_r(r"^azure dns"), DNS),
        (_r(r"^azure front door service[^|]*\|.*\b(policy|rule)\b"), WAF),
        (_r(r"^container registry"), REGISTRO),
        (_r(r"^azure bastion[^|]*\|.*data transfer"), REDE),
        (_r(r"^azure bastion"), BASTION),
        (_r(r"^(microsoft defender|azure defender|azure security center)"), SEGURANCA),
    ),
    "oci": (
        (_r(r"^functions"), SERVERLESS),
        (_r(r"^(key[ _]management|vault|kms)|\|.*key management"), SEGREDOS),
        (_r(r"file storage"), ARQUIVOS),
        (_r(r"^logging|\|.*\blogging\b"), LOGS),
        (_r(r"^monitoring|\|.*\bmonitoring\b"), METRICAS),
        (_r(r"^dns|\|.*networking - dns"), DNS),
        (_r(r"^waf|\|.*web application firewall"), WAF),
        (_r(r"^(cloud guard|vulnerability scanning)"), SEGURANCA),
    ),
}


def reclassificar(provedor: str, servico: str | None, uso: str | None, tipo: str) -> str:
    """O tipo de componente corrigido, ou o mesmo quando nenhuma regra casa."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, novo in REGRAS.get(provedor.lower(), ()):
        if padrao.search(texto):
            return novo
    return tipo
