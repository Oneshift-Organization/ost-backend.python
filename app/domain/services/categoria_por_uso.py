"""Plano de controle, segredos, chaves e serverless separados pelo uso (PLANO_PRECO_NAO_COMPUTE R4).

Irmão do `rede_por_uso`. Três baldes da classificação misturam o que tem preço
comparável com o que não tem:

- `gke_control_plane`: a hora do cluster (EKS, GKE, AKS Standard, OKE Enhanced) é cotada;
  suporte estendido, LTS, plano provisionado e nó virtual ficam ao custo atual;
- `secrets`: segredo por mês, chave por mês e as operações de cada um são cotados;
  rotação, certificado, cofre privado e HSM dedicado ficam ao custo atual;
- `serverless`: Cloud Run, Lambda, Functions e Container Apps, ao custo atual. A
  cobrança (vCPU-segundo ativo e ocioso) não se traduz um a um entre as nuvens;
- `file_storage`: Filestore, EFS, Azure Files e File Storage da OCI, ao custo atual.
  Saíam como disco HDD ou objeto, e o Terraform fazia disco deles (T4).

Cada categoria cotada tem uma unidade, e o destino é cotado pela quantidade medida
vezes a taxa dele. O fator traduz a unidade da linha para a da categoria (requisição
da AWS contada uma a uma, a categoria em 10 mil). Tabela de dados, sem ``if`` por
nuvem. Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.services.rede_por_uso import DEZ_MIL, HORA, MES, converter_quantidade

# Mantém o nome do campo e a chave do payload de sempre: só a hora do cluster
PLANO_DE_CONTROLE = "gke_control_plane"
KUBERNETES_OUTROS = "kubernetes_other"
SEGREDO = "secrets"
SEGREDO_OPERACOES = "secrets_operations"
CHAVE = "key_management"
CHAVE_OPERACOES = "key_management_operations"
SEGURANCA_OUTROS = "security_other"
SERVERLESS = "serverless_compute"
ARQUIVOS = "file_storage"

# Tipo de componente da linha → categoria do que nenhuma regra separa (ao custo atual)
BALDES: Mapping[str, str] = {
    "gke_control_plane": KUBERNETES_OUTROS,
    "secrets": SEGURANCA_OUTROS,
    "serverless": SERVERLESS,
    "file_storage": ARQUIVOS,
}

CATEGORIAS: tuple[str, ...] = (
    PLANO_DE_CONTROLE,
    KUBERNETES_OUTROS,
    SEGREDO,
    SEGREDO_OPERACOES,
    CHAVE,
    CHAVE_OPERACOES,
    SEGURANCA_OUTROS,
    SERVERLESS,
    ARQUIVOS,
)

CHAVE_NO_PAYLOAD: Mapping[str, str] = {
    PLANO_DE_CONTROLE: "gke_plano_controle",
    KUBERNETES_OUTROS: "kubernetes_outros",
    SEGREDO: "segredos",
    SEGREDO_OPERACOES: "segredos_operacoes",
    CHAVE: "chaves",
    CHAVE_OPERACOES: "chaves_operacoes",
    SEGURANCA_OUTROS: "seguranca_outros",
    SERVERLESS: "computacao_serverless",
    ARQUIVOS: "armazenamento_arquivos",
}

# Categoria cotada pela quantidade → unidade dela
UNIDADE: Mapping[str, str] = {
    PLANO_DE_CONTROLE: HORA,
    SEGREDO: MES,
    SEGREDO_OPERACOES: DEZ_MIL,
    CHAVE: MES,
    CHAVE_OPERACOES: DEZ_MIL,
}

_1 = Decimal(1)
_POR_DEZ_MIL = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class TipoPorUso:
    categoria: str
    # Quanto da quantidade da linha vale na unidade da categoria
    fator: Decimal = _1


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(texto, re.IGNORECASE)


_Regras = tuple[tuple[re.Pattern[str], str, Decimal], ...]

# Balde → provedor → (padrão em "serviço | uso", categoria, fator); o primeiro que casa
REGRAS: Mapping[str, Mapping[str, _Regras]] = {
    "gke_control_plane": {
        "aws": ((_r(r"percluster"), PLANO_DE_CONTROLE, _1),),
        "gcp": (
            (_r(r"(zonal|regional|autopilot) kubernetes clusters"), PLANO_DE_CONTROLE, _1),
            (_r(r"clusters? management fee"), PLANO_DE_CONTROLE, _1),
        ),
        "azure": ((_r(r"uptime sla|hosted control plane"), PLANO_DE_CONTROLE, _1),),
        "oci": ((_r(r"enhanced cluster"), PLANO_DE_CONTROLE, _1),),
    },
    "secrets": {
        "aws": (
            (_r(r"secretsmanager-secrets"), SEGREDO, _1),
            (_r(r"secretsmanager-?api ?requests?"), SEGREDO_OPERACOES, _POR_DEZ_MIL),
            (_r(r"kms-keys"), CHAVE, _1),
            (_r(r"kms-requests"), CHAVE_OPERACOES, _POR_DEZ_MIL),
        ),
        "gcp": (
            (_r(r"secret version"), SEGREDO, _1),
            (_r(r"secret access operations"), SEGREDO_OPERACOES, _1),
            (_r(r"key versions"), CHAVE, _1),
            (_r(r"cryptographic operations"), CHAVE_OPERACOES, _1),
        ),
        "azure": (
            (_r(r"key operations"), CHAVE_OPERACOES, _1),
            (_r(r"\|\s*operations\s*$"), SEGREDO_OPERACOES, _1),
            (_r(r"protected .*key"), CHAVE, _1),
        ),
        "oci": ((_r(r"key versions"), CHAVE, _1),),
    },
}


def tipo_por_uso(balde: str, provedor: str, servico: str | None, uso: str | None) -> TipoPorUso:
    """Categoria e fator de uma linha de um dos baldes separados pelo uso."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, categoria, fator in REGRAS.get(balde, {}).get(provedor.lower(), ()):
        if padrao.search(texto):
            return TipoPorUso(categoria, fator)
    return TipoPorUso(BALDES.get(balde, SERVERLESS))


def quantidade_por_uso(
    provedor: str, tipo: TipoPorUso, quantidade: Decimal | None, unidade: str | None
) -> Decimal | None:
    """Quantidade da linha na unidade da categoria, ou None quando não serve."""
    return converter_quantidade(
        UNIDADE.get(tipo.categoria), provedor, tipo.fator, quantidade, unidade
    )
