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
    ),
    "gcp": (
        (_r(r"source repositor[^|]*\| *storage$"), "object_storage"),
        (_r(r"^cloud run[^|]*\|(?!.*(data transfer|egress))"), SERVERLESS),
        (_r(r"^(secret manager|cloud key management)"), SEGREDOS),
        (_r(r"\|.*cdn.*lookup"), REDE),
        (_r(r"filestore"), ARQUIVOS),
    ),
    "azure": (
        (
            _r(r"^(functions|azure container apps)[^|]*\|(?!.*(data transfer|bandwidth))"),
            SERVERLESS,
        ),
        (_r(r"^key vault"), SEGREDOS),
        (_r(r"^(storage|azure files)[^|]*\|.*\bfiles?\b"), ARQUIVOS),
    ),
    "oci": (
        (_r(r"^functions"), SERVERLESS),
        (_r(r"^(key[ _]management|vault|kms)|\|.*key management"), SEGREDOS),
        (_r(r"file storage"), ARQUIVOS),
    ),
}


def reclassificar(provedor: str, servico: str | None, uso: str | None, tipo: str) -> str:
    """O tipo de componente corrigido, ou o mesmo quando nenhuma regra casa."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, novo in REGRAS.get(provedor.lower(), ()):
        if padrao.search(texto):
            return novo
    return tipo
