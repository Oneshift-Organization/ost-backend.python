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

PLANO_CONFERENCIA_DE_SERVICOS (C3 e C4):
- **banco que a linha não nomeia**: DynamoDB (`USE1-ReadCapacityUnit-Hrs`), OpenSearch
  (`ESInstance:r6g.large`), AlloyDB ("Instance vCPU Cores"), Bigtable ("Server Node"),
  Spanner, Firestore e Memorystore saíam não classificados, ou o armazenamento do
  DynamoDB como objeto. A transferência de dados deles segue como rede;
- **contêiner por duração**: Container Instances da Azure e pods do GKE Autopilot são
  contêiner serverless (`categoria_por_uso`), não órfãos.

PLANO_CONFERENCIA_DE_SERVICOS (lote 2):
- **plano do App Service** com tamanho publicado é compute (o parser grava o tamanho);
- **API Gateway** (REST e HTTP da AWS, API Management, API Gateway da OCI) e
  **requisição de objeto** (S3, Blob, Cloud Storage, Object Storage da OCI) têm balde
  próprio, cotados pela chamada (`categoria_por_uso`).

PLANO_CONFERENCIA_DE_SERVICOS (lote 3, §8):
- **Functions** (Lambda, Azure Functions, Cloud Run functions, Oracle Functions) têm
  balde próprio, cotadas por GB-segundo e por chamada; saíam em `serverless`, ao custo
  atual;
- **link dedicado** (Direct Connect, ExpressRoute, Cloud Interconnect, FastConnect) e
  **endpoint privado** (VPC Endpoint, Private Endpoint da Azure, Private Service Connect)
  têm balde próprio. A saída pelo ExpressRoute e pelo Interconnect caía no egress de
  internet.

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
BANCO = "database"
SUPORTE = "support"
COMPUTE = "core"
API = "api_gateway"
OBJETO = "object_requests"
FUNCOES = "functions"
LINK_DEDICADO = "dedicated_link"
ENDPOINT_PRIVADO = "private_endpoint"
# Medidor de plano do App Service com tamanho publicado (`azure_shape._PLANOS_APP_SERVICE`)
_PLANO_APP_SERVICE = (
    r"(b[1-3]|s[1-3]|p[1-3]|p[1-3]\s*v2|p[0-3]\s*v3|p[1-5]mv3|i[1-3]|i[1-6]\s*v2|i[1-5]mv2|pc[2-4])"
    r"\s+app$"
)
# Transferência de dados do próprio serviço: continua rede
_SEM_TRANSFERENCIA = r"(?!.*(datatransfer|data transfer|network|egress))"

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
        (_r(r"^aws ?lambda[^|]*\|(?!.*(datatransfer|-bytes))"), FUNCOES),
        (_r(r"^(aws ?direct ?connect|awsdirectconnect)"), LINK_DEDICADO),
        (_r(r"vpcendpoint-(hours|bytes)$"), ENDPOINT_PRIVADO),
        (_r(r"^(aws ?secrets ?manager|aws ?key ?management|awskms)"), SEGREDOS),
        (_r(r"^(amazon ?efs|amazon elastic file system)"), ARQUIVOS),
        (_r(r"^amazon ?cloudwatch[^|]*\|.*" + _LOG_DA_CLOUDWATCH), LOGS),
        (_r(r"^amazon ?cloudwatch"), METRICAS),
        (_r(r"^(amazon ?prometheus|amazon managed service for prometheus)"), METRICAS),
        (_r(r"^amazon ?route ?53"), DNS),
        (_r(r"^(aws ?waf|awswaf)"), WAF),
        (_r(r"^(amazon ?ecr|amazon elastic container registry)[^|]*\|.*timedstorage"), REGISTRO),
        (_r(r"^(amazon ?guardduty|amazon ?inspector|aws ?security ?hub)"), SEGURANCA),
        (_r(r"^(amazon ?api ?gateway)[^|]*\|(?!.*(datatransfer|-bytes))"), API),
        (_r(r"^(amazon ?s3|amazon simple storage service)[^|]*\|.*\brequests-"), OBJETO),
        # Plano de suporte (Business, Enterprise, Developer, Enterprise On-Ramp)
        (_r(r"^(aws ?support|aws ?developer ?support|aws ?enterprise ?on ?ramp)"), SUPORTE),
        (_r(r"^(amazon ?dynamodb)[^|]*\|" + _SEM_TRANSFERENCIA), BANCO),
        (
            _r(
                r"^(amazon ?es|amazon ?opensearch( service)?|amazon elasticsearch service)[^|]*\|"
                + _SEM_TRANSFERENCIA
            ),
            BANCO,
        ),
    ),
    "gcp": (
        (_r(r"source repositor[^|]*\| *storage$"), "object_storage"),
        (_r(r"^(cloud run functions|cloud functions)[^|]*\|(?!.*(data transfer|egress))"), FUNCOES),
        (_r(r"^cloud run[^|]*\|(?!.*(data transfer|egress))"), SERVERLESS),
        (_r(r"\|\s*(cloud|cross-cloud) interconnect\b"), LINK_DEDICADO),
        (_r(r"\|.*\bprivate service connect\b"), ENDPOINT_PRIVADO),
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
        (
            _r(
                r"^(alloydb|cloud bigtable|cloud spanner|cloud firestore|firestore|cloud datastore"
                r"|cloud memorystore[^|]*)\s*\|" + _SEM_TRANSFERENCIA
            ),
            BANCO,
        ),
        (_r(r"^kubernetes engine[^|]*\|\s*autopilot"), SERVERLESS),
        (_r(r"^(google cloud support|cloud support|support)\s*\|"), SUPORTE),
        (_r(r"^cloud storage[^|]*\|.*class [ab] operations"), OBJETO),
    ),
    "azure": (
        (_r(r"^functions[^|]*\|(?!.*(data transfer|bandwidth))"), FUNCOES),
        (_r(r"^expressroute"), LINK_DEDICADO),
        (_r(r"^virtual network[^|]*\|.*\bprivate link\b"), ENDPOINT_PRIVADO),
        (
            _r(
                r"^(azure container apps|container instances)[^|]*\|"
                r"(?!.*(data transfer|bandwidth))"
            ),
            SERVERLESS,
        ),
        (_r(r"^key vault"), SEGREDOS),
        (_r(r"^(storage|azure files)[^|]*\|.*\bfiles?\b"), ARQUIVOS),
        (_r(r"^log analytics|^azure monitor[^|]*\|.*\blogs\b"), LOGS),
        (_r(r"^azure monitor"), METRICAS),
        (_r(r"^azure dns"), DNS),
        (_r(r"^azure front door service[^|]*\|.*\b(policy|rule)\b"), WAF),
        (_r(r"^container registry"), REGISTRO),
        (_r(r"^azure app service[^|]*\|.*\b" + _PLANO_APP_SERVICE), COMPUTE),
        (_r(r"^api management[^|]*\|(?!.*(data transfer|bandwidth))"), API),
        (_r(r"^storage[^|]*\|.*\bblob\b[^|]*\|[^|]*operations$"), OBJETO),
        (_r(r"^azure bastion[^|]*\|.*data transfer"), REDE),
        (_r(r"^azure bastion"), BASTION),
        (_r(r"^(microsoft defender|azure defender|azure security center)"), SEGURANCA),
    ),
    "oci": (
        (_r(r"^functions"), FUNCOES),
        (_r(r"\|\s*oci - fastconnect\b"), LINK_DEDICADO),
        (_r(r"^(key[ _]management|vault|kms)|\|.*key management"), SEGREDOS),
        (_r(r"file storage"), ARQUIVOS),
        (_r(r"^logging|\|.*\blogging\b"), LOGS),
        (_r(r"^monitoring|\|.*\bmonitoring\b"), METRICAS),
        (_r(r"^dns|\|.*networking - dns"), DNS),
        (_r(r"^waf|\|.*web application firewall"), WAF),
        (_r(r"^(cloud guard|vulnerability scanning)"), SEGURANCA),
        (_r(r"\|\s*api gateway\b"), API),
        (_r(r"\|\s*object storage - requests"), OBJETO),
    ),
}


def reclassificar(provedor: str, servico: str | None, uso: str | None, tipo: str) -> str:
    """O tipo de componente corrigido, ou o mesmo quando nenhuma regra casa."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, novo in REGRAS.get(provedor.lower(), ()):
        if padrao.search(texto):
            return novo
    return tipo
