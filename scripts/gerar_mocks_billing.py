#!/usr/bin/env python3
"""Gera mocks de export detalhado a partir dos bills reais versionados.

Enquanto não chega um CUR ou um export do BigQuery de verdade, o pipeline é
exercitado com mocks. O risco de mock é refletir o nosso entendimento em vez do
formato real, então este gerador segue três regras:

1. **Colunas e semântica da documentação oficial**, não do nosso parser
   (fontes em ``PLANO_CUR_BIGQUERY.md`` §1). O parser é que tem de se adaptar ao
   mock, nunca o contrário.
2. **O dinheiro se conserva.** Cada linha do bill real vira N linhas no mock, e
   a soma volta exatamente ao valor de origem. Quem lê o mock consegue
   reconciliá-lo com o bill de onde saiu. Os testes de invariante
   (``test_mocks_billing.py``) verificam isso com pandas direto, sem o parser.
3. **Determinístico.** Nenhum ``random``: ids e distribuições saem de hash
   sobre a origem. Rodar duas vezes dá byte a byte o mesmo arquivo.

O que é INVENTADO (o bill real não informa), e fica declarado aqui:

- preço sob demanda por tipo (``_PRECO_POR_NU``): aproximação do preço público
  us-east-1, usada só para dar valor sob demanda às horas cobertas por
  compromisso e para ratear o compromisso entre elas. Nenhum número de produção
  sai daqui;
- quantas instâncias existem por tipo (⌈horas ÷ horas-por-instância⌉), contas
  de uso, tags, zonas, nomes de recurso;
- a divisão das horas entre sob demanda e coberta: horas sob demanda =
  ``custo real ÷ preço sob demanda``, o que deixa a linha ``Usage`` com
  exatamente o custo do bill real;
- utilização do Savings Plan (95%) e desconto mínimo de reserva (20%).

Uso::

    python scripts/gerar_mocks_billing.py            # grava em fixtures/mocks
    python scripts/gerar_mocks_billing.py --destino /tmp/mocks
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = RAIZ / "app/tests/integration/fixtures"
DESTINO_PADRAO = FIXTURES / "mocks"

_Q = Decimal("1E-10")
_ZERO = Decimal("0")


# ── utilidades ──────────────────────────────────────────────────────────────


def _d(valor: str | None) -> Decimal:
    return Decimal(valor) if valor not in (None, "") else _ZERO


def _dm(valor: str | None) -> Decimal:
    """Número do Cost Table do GCP: vem com separador de milhar ("3,141.806")."""
    return _d((valor or "").replace(",", ""))


def _q(valor: Decimal) -> Decimal:
    return valor.quantize(_Q, rounding=ROUND_HALF_EVEN)


def _fmt(valor: Decimal | None) -> str:
    """Número como o CUR entrega: ponto decimal, sem notação científica."""
    if valor is None:
        return ""
    texto = format(_q(valor), "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return "0" if texto in ("-0", "") else texto


def _hash(*partes: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()


def _escolha(opcoes: list, *partes: object):
    return opcoes[int(_hash(*partes)[:8], 16) % len(opcoes)]


def repartir(total: Decimal, pesos: list[Decimal]) -> list[Decimal]:
    """Reparte ``total`` na proporção dos pesos, **sem perder centavo**.

    Cada parte é arredondada em 10 casas e a diferença do arredondamento vai
    para a maior parte. Sem isso a soma do mock não voltaria ao bill real, e o
    invariante de conservação falharia por ruído, não por erro.
    """
    if not pesos:
        return []
    soma = sum(pesos, _ZERO)
    if soma == 0:
        pesos = [Decimal("1")] * len(pesos)
        soma = Decimal(len(pesos))
    partes = [_q(total * p / soma) for p in pesos]
    maior = max(range(len(pesos)), key=lambda i: pesos[i])
    partes[maior] += total - sum(partes, _ZERO)
    return partes


# ── AWS: tabelas de referência (declaradas como aproximação) ────────────────

INICIO = datetime(2026, 3, 1, tzinfo=UTC)
DIAS = 31
HORAS_MES = Decimal(DIAS * 24)
FIM = INICIO + timedelta(days=DIAS)

PAGADOR = ("111111111111", "OneShift Demo Payer")
# Conta de uso por recurso. Produção pesa mais, como num cliente típico.
CONTAS = [
    ("222222222222", "producao", "prod"),
    ("222222222222", "producao", "prod"),
    ("222222222222", "producao", "prod"),
    ("333333333333", "homologacao", "staging"),
    ("444444444444", "dados", "data"),
]
APPS = ["checkout", "catalogo", "pagamentos", "auth", "busca", "relatorios", "ingestao"]
TIMES = ["plataforma", "dados", "produto", "sre"]

# Prefixo de região do UsageType → código de região. Sem prefixo = us-east-1.
_REGIOES = {
    "USE1": "us-east-1",
    "USE2": "us-east-2",
    "USW1": "us-west-1",
    "USW2": "us-west-2",
    "SAE1": "sa-east-1",
    "CAN1": "ca-central-1",
    "EU": "eu-west-1",
    "EUC1": "eu-central-1",
    "EUC2": "eu-central-2",
    "EUW2": "eu-west-2",
    "EUW3": "eu-west-3",
    "EUN1": "eu-north-1",
    "EUS1": "eu-south-1",
    "EUS2": "eu-south-2",
    "ESC1": "eu-south-2",
    "APN1": "ap-northeast-1",
    "APN2": "ap-northeast-2",
    "APN3": "ap-northeast-3",
    "APS1": "ap-southeast-1",
    "APS2": "ap-southeast-2",
    "APS3": "ap-south-1",
    "APS4": "ap-southeast-3",
    "APS5": "ap-south-2",
    "APS7": "ap-southeast-5",
    "APS8": "ap-southeast-7",
    "APS9": "ap-southeast-4",
    "APE1": "ap-east-1",
    "APE2": "ap-east-2",
    "AFS1": "af-south-1",
    "MES1": "me-south-1",
    "UGW1": "us-gov-west-1",
    "UGE1": "us-gov-east-1",
}
_PREFIXO = re.compile(r"^([A-Z]{2,4}\d?)[-:]")

# Fator de normalização da AWS por tamanho. O preço dentro de uma família é
# linear nele — é assim que a AWS precifica e que as RIs flexíveis funcionam.
_NF = {
    "nano": Decimal("0.25"),
    "micro": Decimal("0.5"),
    "small": Decimal("1"),
    "medium": Decimal("2"),
    "large": Decimal("4"),
    "xlarge": Decimal("8"),
}

# US$ por unidade de normalização-hora, Linux, us-east-1. APROXIMAÇÃO do preço
# público (preço do `.large` ÷ 4). Serve só ao mock — ver docstring do módulo.
_PRECO_POR_NU = {
    "c4": "0.025",
    "c5a": "0.01925",
    "c7a": "0.02566",
    "c7g": "0.018125",
    "c7gn": "0.0312",
    "c7i-flex": "0.02119",
    "c8g": "0.01994",
    "c8gn": "0.0342",
    "is4gen": "0.072",
    "m5a": "0.0215",
    "m6a": "0.0216",
    "m7a": "0.02898",
    "m7g": "0.0204",
    "m8a": "0.0277",
    "m8g": "0.02244",
    "m8gb": "0.0280",
    "r5": "0.0315",
    "r5dn": "0.0418",
    "r6g": "0.0252",
    "r7g": "0.026775",
    "r8g": "0.029455",
    "t2": "0.0232",
    "t3": "0.0208",
    "t3a": "0.0188",
    "t4g": "0.0168",
    # RDS single-AZ PostgreSQL (Multi-AZ = 2x)
    "db.t3": "0.036",
    "db.t4g": "0.032",
    "db.m6g": "0.038",
    "db.m7g": "0.042",
    "db.m8g": "0.044",
    "db.r8g": "0.066",
    # ElastiCache
    "cache.t4g": "0.032",
    "cache.m6g": "0.03725",
    "cache.m7g": "0.0395",
    "cache.r7g": "0.05475",
    "cache.c7gn": "0.064",
}
_PRECO_PADRAO = Decimal("0.025")

# (vCPU, GiB) medidos — os specs publicados de cada tipo que aparece no bill.
# É o que o CUR entrega no mapa `product`; o parser deve preferi-los à
# estimativa pelo nome (RN4).
_SPECS: dict[str, tuple[str, str]] = {
    "c4.4xlarge": ("16", "30"),
    "c5a.large": ("2", "4"),
    "c7a.2xlarge": ("8", "16"),
    "c7g.large": ("2", "4"),
    "c7g.xlarge": ("4", "8"),
    "c7g.2xlarge": ("8", "16"),
    "c7g.4xlarge": ("16", "32"),
    "c7gn.large": ("2", "4"),
    "c7gn.xlarge": ("4", "8"),
    "c7gn.2xlarge": ("8", "16"),
    "c7i-flex.xlarge": ("4", "8"),
    "c8g.xlarge": ("4", "8"),
    "c8g.2xlarge": ("8", "16"),
    "c8gn.large": ("2", "4"),
    "is4gen.xlarge": ("4", "24"),
    "m5a.large": ("2", "8"),
    "m5a.2xlarge": ("8", "32"),
    "m6a.xlarge": ("4", "16"),
    "m6a.24xlarge": ("96", "384"),
    "m7a.xlarge": ("4", "16"),
    "m7a.2xlarge": ("8", "32"),
    "m7g.medium": ("1", "4"),
    "m7g.large": ("2", "8"),
    "m7g.xlarge": ("4", "16"),
    "m7g.2xlarge": ("8", "32"),
    "m7g.4xlarge": ("16", "64"),
    "m8a.2xlarge": ("8", "32"),
    "m8g.xlarge": ("4", "16"),
    "m8g.2xlarge": ("8", "32"),
    "m8gb.large": ("2", "8"),
    "r5.4xlarge": ("16", "128"),
    "r5dn.large": ("2", "16"),
    "r6g.2xlarge": ("8", "64"),
    "r7g.xlarge": ("4", "32"),
    "r7g.4xlarge": ("16", "128"),
    "r8g.large": ("2", "16"),
    "r8g.xlarge": ("4", "32"),
    "t2.micro": ("1", "1"),
    "t2.xlarge": ("4", "16"),
    "t3.small": ("2", "2"),
    "t3.xlarge": ("4", "16"),
    "t3a.nano": ("2", "0.5"),
    "t3a.micro": ("2", "1"),
    "t3a.small": ("2", "2"),
    "t3a.medium": ("2", "4"),
    "t3a.large": ("2", "8"),
    "t3a.xlarge": ("4", "16"),
    "t3a.2xlarge": ("8", "32"),
    "t4g.nano": ("2", "0.5"),
    "t4g.micro": ("2", "1"),
    "t4g.small": ("2", "2"),
    "t4g.medium": ("2", "4"),
    "t4g.large": ("2", "8"),
    "t4g.xlarge": ("4", "16"),
    "db.t3.micro": ("2", "1"),
    "db.t3.small": ("2", "2"),
    "db.t4g.micro": ("2", "1"),
    "db.t4g.small": ("2", "2"),
    "db.t4g.medium": ("2", "4"),
    "db.t4g.large": ("2", "8"),
    "db.t4g.xlarge": ("4", "16"),
    "db.t4g.2xlarge": ("8", "32"),
    "db.m6g.large": ("2", "8"),
    "db.m6g.xlarge": ("4", "16"),
    "db.m6g.2xlarge": ("8", "32"),
    "db.m6g.4xlarge": ("16", "64"),
    "db.m7g.xlarge": ("4", "16"),
    "db.m8g.xlarge": ("4", "16"),
    "db.r8g.xlarge": ("4", "32"),
    "db.r8g.2xlarge": ("8", "64"),
    "db.r8g.4xlarge": ("16", "128"),
    "db.r8g.8xlarge": ("32", "256"),
    "db.r8g.12xlarge": ("48", "384"),
    "db.r8g.16xlarge": ("64", "512"),
    "cache.t4g.micro": ("2", "0.5"),
    "cache.t4g.small": ("2", "1.37"),
    "cache.t4g.medium": ("2", "3.09"),
    "cache.m6g.large": ("2", "6.38"),
    "cache.m6g.xlarge": ("4", "12.93"),
    "cache.m6g.2xlarge": ("8", "26.04"),
    "cache.m7g.large": ("2", "6.38"),
    "cache.m7g.xlarge": ("4", "12.93"),
    "cache.r7g.xlarge": ("4", "26.32"),
    "cache.r7g.2xlarge": ("8", "52.82"),
    "cache.c7gn.2xlarge": ("8", "13.6"),
}

# Operação do RDS → motor. Mapeamento publicado da AWS para os códigos do bill.
_MOTOR_RDS = {
    "CreateDBInstance:0002": ("MySQL", "mysql"),
    "CreateDBInstance:0014": ("PostgreSQL", "postgres"),
    "CreateDBInstance:0020": ("Aurora PostgreSQL", "aurora-postgresql"),
    "CreateDBInstance:0021": ("Aurora PostgreSQL", "aurora-postgresql"),
}

_INSTANCIA = re.compile(
    r"(?:^|-)(BoxUsage|SpotUsage|DedicatedUsage|InstanceUsage|InstanceUsageIOOptimized"
    r"|Multi-AZUsage|NodeUsage):(?P<tipo>[\w.\-]+)$"
)
_TAMANHO = re.compile(r"\.(\d*)(nano|micro|small|medium|large|xlarge|xl)$")
# Recursos cobrados por hora-recurso, 24x7: quantos existem = horas ÷ 744.
_POR_HORA_RECURSO = re.compile(
    r"(NatGateway-Hours|LoadBalancerUsage|VpcEndpoint-Hours|VPN-Usage-Hours"
    r"|AmazonEKS-Hours:perCluster|Kafka\.[a-z0-9]+\.[a-z0-9]+)"
)


_PREFIXO_RECURSO = {
    "NatGateway-Hours": "nat",
    "LoadBalancerUsage": "lb",
    "VpcEndpoint-Hours": "vpce",
    "VPN-Usage-Hours": "vpn",
    "AmazonEKS-Hours:perCluster": "eks",
    "Kafka": "kafka",
}


def _regiao(usage_type: str) -> str:
    m = _PREFIXO.match(usage_type)
    if m and m.group(1) in _REGIOES:
        return _REGIOES[m.group(1)]
    return "us-east-1"


def _tipo_normalizado(bruto: str) -> str:
    """`db.m6g.2xl` → `db.m6g.2xlarge`: o bill abrevia, o CUR não."""
    return re.sub(r"\.(\d*)xl$", r".\1xlarge", bruto)


def _familia(tipo: str) -> str:
    return tipo.rsplit(".", 1)[0]


def _fator(tipo: str) -> Decimal:
    m = _TAMANHO.search(tipo)
    if not m:
        return Decimal("4")
    mult, tamanho = m.group(1), m.group(2)
    if tamanho in ("xlarge", "xl"):
        return Decimal("8") * (Decimal(mult) if mult else Decimal("1"))
    return _NF[tamanho]


def _preco_sob_demanda(tipo: str, multi_az: bool) -> Decimal:
    base = Decimal(_PRECO_POR_NU.get(_familia(tipo), str(_PRECO_PADRAO)))
    preco = base * _fator(tipo)
    return preco * 2 if multi_az else preco


def _specs(tipo: str) -> tuple[str, str] | None:
    return _SPECS.get(tipo)


# ── AWS: modelo intermediário ───────────────────────────────────────────────


@dataclass
class Compromisso:
    """Savings Plan ou reserva do bill real, com a taxa mensal verdadeira."""

    tipo: str  # "sp" | "ri"
    arn: str
    usage_type: str
    product_code: str
    taxa_mensal: Decimal
    familia: str | None  # None = Compute SP (qualquer família)
    operacao: str = ""
    oferta: str = ""
    prazo: str = ""
    cobertas: list[dict] = field(default_factory=list)
    usado: Decimal = _ZERO


@dataclass
class ResumoAWS:
    total_real: Decimal
    imposto_real: Decimal
    linhas: int
    instancias: int
    compromissos: dict[str, dict[str, str]]


_COLUNAS_CUR2 = [
    "identity_line_item_id",
    "identity_time_interval",
    "bill_bill_type",
    "bill_billing_entity",
    "bill_billing_period_end_date",
    "bill_billing_period_start_date",
    "bill_invoice_id",
    "bill_invoicing_entity",
    "bill_payer_account_id",
    "bill_payer_account_name",
    "cost_category",
    "discount",
    "line_item_availability_zone",
    "line_item_currency_code",
    "line_item_legal_entity",
    "line_item_line_item_description",
    "line_item_line_item_type",
    "line_item_normalization_factor",
    "line_item_normalized_usage_amount",
    "line_item_operation",
    "line_item_product_code",
    "line_item_resource_id",
    "line_item_tax_type",
    "line_item_unblended_cost",
    "line_item_unblended_rate",
    "line_item_usage_account_id",
    "line_item_usage_account_name",
    "line_item_usage_amount",
    "line_item_usage_end_date",
    "line_item_usage_start_date",
    "line_item_usage_type",
    "pricing_currency",
    "pricing_public_on_demand_cost",
    "pricing_public_on_demand_rate",
    "pricing_term",
    "pricing_unit",
    "product",
    "product_instance_family",
    "product_instance_type",
    "product_location",
    "product_operation",
    "product_product_family",
    "product_region_code",
    "product_servicecode",
    "product_sku",
    "product_usagetype",
    "reservation_amortized_upfront_cost_for_usage",
    "reservation_amortized_upfront_fee_for_billing_period",
    "reservation_effective_cost",
    "reservation_recurring_fee_for_usage",
    "reservation_reservation_a_r_n",
    "reservation_unused_amortized_upfront_fee_for_billing_period",
    "reservation_unused_quantity",
    "reservation_unused_recurring_fee",
    "resource_tags",
    "savings_plan_amortized_upfront_commitment_for_billing_period",
    "savings_plan_offering_type",
    "savings_plan_payment_option",
    "savings_plan_purchase_term",
    "savings_plan_recurring_commitment_for_billing_period",
    "savings_plan_region",
    "savings_plan_savings_plan_a_r_n",
    "savings_plan_savings_plan_effective_cost",
    "savings_plan_savings_plan_rate",
    "savings_plan_total_commitment_to_date",
    "savings_plan_used_commitment",
]


def _iso(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%SZ")


def _unidade(usage_type: str) -> str:  # noqa: PLR0911 - cascata de padrões
    ut = usage_type.lower()
    if "cpucredits" in ut:
        return "vCPU-Hours"
    if "iops" in ut:
        return "IOPS-Mo"
    if "throughput" in ut:
        return "GiBps-mo"
    if "bytehrs" in ut or "volumeusage" in ut or "snapshotusage" in ut or "storage" in ut:
        return "GB-Mo"
    if "bytes" in ut:
        return "GB"
    if "request" in ut:
        return "Requests"
    if "gb-second" in ut:
        return "Lambda-GB-Second"
    if any(t in ut for t in ("usage:", "hours", "hrs", "nodeusage", "ebsoptimized", "kafka.")):
        return "Hrs"
    return "Units"


def _familia_produto(product_code: str, usage_type: str) -> str:  # noqa: PLR0911
    ut = usage_type.lower()
    if _INSTANCIA.search(usage_type):
        if product_code == "AmazonRDS":
            return "Database Instance"
        if product_code == "AmazonElastiCache":
            return "Cache Instance"
        return "Compute Instance"
    if "volumeusage" in ut or "volumep-" in ut:
        return "Storage"
    if "snapshot" in ut:
        return "Storage Snapshot"
    if "timedstorage" in ut:
        return "Storage"
    if "bytes" in ut:
        return "Data Transfer"
    if "request" in ut:
        return "API Request"
    if "natgateway" in ut:
        return "NAT Gateway"
    if "storage" in ut:
        return "Database Storage"
    return "Other"


class GeradorCUR:
    """Converte o bill agregado por UsageType num CUR 2.0 diário com recursos."""

    def __init__(self, origem: Path) -> None:
        self._origem = origem
        self._linhas: list[dict[str, str]] = []
        self._recursos: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._compromissos: list[Compromisso] = []
        self._contador = 0

    # ── entrada ─────────────────────────────────────────────────────────

    def _ler_origem(self) -> list[dict[str, str]]:
        with self._origem.open(newline="", encoding="utf-8") as fh:
            return [r for r in csv.DictReader(fh) if r["RecordType"] == "PayerLineItem"]

    # ── construção de linhas ────────────────────────────────────────────

    def _linha(
        self,
        *,
        product_code: str,
        usage_type: str,
        operacao: str,
        tipo_linha: str,
        custo: Decimal,
        quantidade: Decimal,
        inicio: datetime,
        fim: datetime,
        recurso: dict | None = None,
        **extra: str,
    ) -> dict[str, str]:
        self._contador += 1
        regiao = recurso["regiao"] if recurso else _regiao(usage_type)
        conta, nome_conta = (
            (recurso["conta"], recurso["nome_conta"]) if recurso else (CONTAS[0][0], CONTAS[0][1])
        )
        linha = dict.fromkeys(_COLUNAS_CUR2, "")
        linha.update(
            {
                "identity_line_item_id": _hash("li", self._contador, usage_type)[:40],
                "identity_time_interval": f"{_iso(inicio)}/{_iso(fim)}",
                "bill_bill_type": "Anniversary",
                "bill_billing_entity": "AWS",
                "bill_billing_period_start_date": _iso(INICIO),
                "bill_billing_period_end_date": _iso(FIM),
                # Março/2026 está fechado: a fatura existe. Mês aberto viria
                # vazio, e o parser marcaria tudo como estimativa.
                "bill_invoice_id": "2026031509876543",
                "bill_invoicing_entity": "Amazon Web Services, Inc.",
                "bill_payer_account_id": PAGADOR[0],
                "bill_payer_account_name": PAGADOR[1],
                "cost_category": "{}",
                "discount": "{}",
                "line_item_currency_code": "USD",
                "line_item_legal_entity": "Amazon Web Services, Inc.",
                "line_item_line_item_type": tipo_linha,
                "line_item_operation": operacao,
                "line_item_product_code": product_code,
                "line_item_unblended_cost": _fmt(custo),
                "line_item_usage_account_id": conta,
                "line_item_usage_account_name": nome_conta,
                "line_item_usage_amount": _fmt(quantidade),
                "line_item_usage_start_date": _iso(inicio),
                "line_item_usage_end_date": _iso(fim),
                "line_item_usage_type": usage_type,
                "pricing_currency": "USD",
                "pricing_term": "OnDemand",
                "pricing_unit": _unidade(usage_type),
                "product_operation": operacao,
                "product_product_family": _familia_produto(product_code, usage_type),
                "product_region_code": regiao,
                "product_servicecode": product_code,
                "product_sku": _hash("sku", usage_type, operacao)[:16].upper(),
                "product_usagetype": usage_type,
                "resource_tags": "{}",
                "product": json.dumps({"region": regiao, "servicename": product_code}),
            }
        )
        if recurso:
            linha["line_item_resource_id"] = recurso["id"]
            linha["line_item_availability_zone"] = recurso["zona"]
            linha["resource_tags"] = json.dumps(recurso["tags"], sort_keys=True)
            linha["product"] = json.dumps(recurso["produto"], sort_keys=True)
            if recurso.get("tipo"):
                linha["product_instance_type"] = recurso["tipo"]
                linha["product_instance_family"] = recurso["familia_produto"]
        linha.update(extra)
        self._linhas.append(linha)
        return linha

    def _novo_recurso(
        self,
        prefixo: str,
        chave: str,
        indice: int,
        regiao: str,
        *,
        tipo: str | None = None,
        produto: dict | None = None,
        arn: str | None = None,
    ) -> dict:
        conta, nome_conta, env = _escolha(CONTAS, chave, indice)
        app = _escolha(APPS, chave, indice, "app")
        if arn:
            # O hash entra no nome: dois tipos de uso (InstanceUsage e
            # Multi-AZUsage do mesmo tamanho) gerariam o mesmo ARN só com app+índice.
            ident = arn.format(regiao=regiao, conta=conta, n=f"{app}-{_hash(chave, indice)[:6]}")
        else:
            ident = f"{prefixo}-{_hash(chave, indice)[:17]}"
        recurso = {
            "id": ident,
            "regiao": regiao,
            "zona": regiao + _escolha(["a", "b", "c"], chave, indice, "az"),
            "conta": conta,
            "nome_conta": nome_conta,
            "tipo": tipo,
            "familia_produto": _familia(tipo) if tipo else "",
            "tags": {
                "user_env": env,
                "user_app": app,
                "user_team": _escolha(TIMES, chave, indice, "time"),
            },
            "produto": {"region": regiao, **(produto or {})},
        }
        return recurso

    # ── instâncias (EC2, RDS, ElastiCache) ──────────────────────────────

    def _instancias(self, origem: dict[str, str], tipo_bruto: str) -> None:
        usage_type = origem["UsageType"]
        product_code = origem["ProductCode"]
        operacao = origem["Operation"]
        horas = _d(origem["UsageQuantity"])
        custo = _d(origem["TotalCost"])
        if horas <= 0:
            return

        tipo = _tipo_normalizado(tipo_bruto)
        multi_az = "Multi-AZ" in usage_type
        spot = "SpotUsage" in usage_type
        regiao = _regiao(usage_type)
        preco = _preco_sob_demanda(tipo, multi_az)

        compromisso = None if spot else self._compromisso_para(product_code, tipo, operacao)
        horas_sd = horas if compromisso is None else min(horas, custo / preco)
        if horas_sd == horas:
            # Nada coberto: se a tarifa observada passa da tabela, a tabela é
            # que está baixa — e valor sob demanda abaixo do custo real seria
            # um "desconto negativo". Spot fica abaixo da tabela, como deve.
            preco = max(preco, custo / horas)

        # Burstable de EC2 roda em horário comercial (12h/dia); o resto, 24x7.
        # É o que faz a "fração de tempo ativo" do pool ter o que medir.
        comercial = product_code == "AmazonEC2" and tipo.split(".")[0] in {"t2", "t3", "t3a", "t4g"}
        por_dia = Decimal("12") if comercial else Decimal("24")
        teto = por_dia * DIAS
        n = max(1, math.ceil(horas / teto))

        produto = self._produto_instancia(product_code, tipo, operacao, multi_az)
        arn = None
        prefixo = "i"
        if product_code == "AmazonRDS":
            slug = _MOTOR_RDS.get(operacao, ("", "db"))[1]
            arn = "arn:aws:rds:{regiao}:{conta}:db:" + slug + "-{n}"
        elif product_code == "AmazonElastiCache":
            arn = "arn:aws:elasticache:{regiao}:{conta}:cluster:{n}-001"

        restantes = horas
        for i in range(n):
            h_inst = min(teto, restantes)
            restantes -= h_inst
            recurso = self._novo_recurso(
                prefixo, usage_type, i, regiao, tipo=tipo, produto=produto, arn=arn
            )
            recurso["horas"] = h_inst
            self._recursos[(product_code, regiao)].append(recurso)
            self._dias_da_instancia(
                recurso=recurso,
                h_inst=h_inst,
                por_dia=por_dia,
                horas_total=horas,
                horas_sd_total=horas_sd,
                custo_total=custo,
                preco=preco,
                product_code=product_code,
                usage_type=usage_type,
                operacao=operacao,
                compromisso=compromisso,
                spot=spot,
            )

    def _dias_da_instancia(
        self,
        *,
        recurso: dict,
        h_inst: Decimal,
        por_dia: Decimal,
        horas_total: Decimal,
        horas_sd_total: Decimal,
        custo_total: Decimal,
        preco: Decimal,
        product_code: str,
        usage_type: str,
        operacao: str,
        compromisso: Compromisso | None,
        spot: bool,
    ) -> None:
        # Instância parcial nasce no meio do mês e está viva no fim: é o que um
        # inventário atual enxergaria.
        dias_cheios = int(h_inst // por_dia)
        resto = h_inst - por_dia * dias_cheios
        n_dias = dias_cheios + (1 if resto > 0 else 0)
        primeiro = DIAS - n_dias
        frac_sd = horas_sd_total / horas_total
        descricao_sd = (
            f"${_fmt(preco)} per {'Spot' if spot else 'On Demand'} Linux "
            f"{recurso['tipo']} Instance Hour"
        )
        for k in range(n_dias):
            dia = INICIO + timedelta(days=primeiro + k)
            h = resto if (k == 0 and resto > 0) else por_dia
            h_sd = _q(h * frac_sd)
            h_cob = h - h_sd
            comum = {
                "product_code": product_code,
                "usage_type": usage_type,
                "operacao": operacao,
                "inicio": dia,
                "fim": dia + timedelta(days=1),
                "recurso": recurso,
                "line_item_normalization_factor": _fmt(_fator(recurso["tipo"])),
                "pricing_public_on_demand_rate": _fmt(preco),
            }
            if h_sd > 0:
                self._linha(
                    tipo_linha="Usage",
                    custo=_ZERO,  # custo real repartido depois, sem perder centavo
                    quantidade=h_sd,
                    line_item_line_item_description=descricao_sd,
                    line_item_unblended_rate=_fmt(preco),
                    pricing_public_on_demand_cost=_fmt(h_sd * preco),
                    **comum,
                )["_peso_sd"] = str(h_sd)
                self._linhas[-1]["_origem"] = f"{usage_type}|{operacao}"
            if h_cob > 0 and compromisso is not None:
                linha = self._linha(
                    tipo_linha=(
                        "SavingsPlanCoveredUsage" if compromisso.tipo == "sp" else "DiscountedUsage"
                    ),
                    custo=(h_cob * preco) if compromisso.tipo == "sp" else _ZERO,
                    quantidade=h_cob,
                    line_item_line_item_description=(
                        f"${_fmt(preco)} per On Demand Linux {recurso['tipo']} Instance Hour"
                        if compromisso.tipo == "sp"
                        else f"{recurso['tipo']} reserved instance applied"
                    ),
                    line_item_unblended_rate=_fmt(preco) if compromisso.tipo == "sp" else "0",
                    pricing_public_on_demand_cost=_fmt(h_cob * preco),
                    pricing_term="OnDemand" if compromisso.tipo == "sp" else "Reserved",
                    **comum,
                )
                linha["_sob_demanda"] = str(h_cob * preco)
                compromisso.cobertas.append(linha)
        # Marcador para repartir o custo real entre as linhas sob demanda. A
        # chave inclui a operação: o bill tem `Multi-AZUsage:db.t3.micro` duas
        # vezes, uma de MySQL e outra de PostgreSQL, com custos diferentes.
        recurso.setdefault("_origens", []).append((f"{usage_type}|{operacao}", custo_total))

    def _produto_instancia(
        self, product_code: str, tipo: str, operacao: str, multi_az: bool
    ) -> dict:
        specs = _specs(tipo)
        produto: dict[str, str] = {"instance_type": tipo}
        if specs:
            produto["vcpu"] = specs[0]
            produto["memory"] = f"{specs[1]} GiB"
        if product_code == "AmazonEC2":
            produto.update({"operating_system": "Linux", "tenancy": "Shared"})
        elif product_code == "AmazonRDS":
            produto["database_engine"] = _MOTOR_RDS.get(operacao, ("Unknown", ""))[0]
            produto["deployment_option"] = "Multi-AZ" if multi_az else "Single-AZ"
        elif product_code == "AmazonElastiCache":
            produto["cache_engine"] = "Redis"
        return produto

    def _compromisso_para(self, product_code: str, tipo: str, operacao: str) -> Compromisso | None:
        familia = _familia(tipo)
        if product_code == "AmazonEC2":
            especifico = [c for c in self._compromissos if c.tipo == "sp" and c.familia == familia]
            if especifico:
                return especifico[0]
            geral = [c for c in self._compromissos if c.tipo == "sp" and c.familia is None]
            return geral[0] if geral else None
        candidatos = [
            c
            for c in self._compromissos
            if c.tipo == "ri"
            and c.product_code == product_code
            and c.familia == familia
            and (product_code != "AmazonRDS" or c.operacao == operacao)
        ]
        return candidatos[0] if candidatos else None

    # ── compromissos ────────────────────────────────────────────────────

    def _registrar_compromissos(self, origem: list[dict[str, str]]) -> None:
        for r in origem:
            ut = r["UsageType"]
            if r["ProductCode"] == "Savings Plans for AWS Compute usage":
                m = re.match(r"EC2SP:([a-z0-9]+)\.(\d)yr", ut)
                prazo = re.search(r"(\d)yr", ut)
                self._compromissos.append(
                    Compromisso(
                        tipo="sp",
                        arn=f"arn:aws:savingsplans::{PAGADOR[0]}:savingsplan/{_hash(ut)[:8]}",
                        usage_type=ut,
                        product_code="ComputeSavingsPlans",
                        taxa_mensal=_d(r["TotalCost"]),
                        familia=m.group(1) if m else None,
                        oferta="EC2InstanceSavingsPlans" if m else "ComputeSavingsPlans",
                        prazo=f"{prazo.group(1)}yr" if prazo else "1yr",
                    )
                )
            elif "HeavyUsage:" in ut and _d(r["TotalCost"]) > 0:
                tipo = _tipo_normalizado(ut.split("HeavyUsage:", 1)[1])
                familia = _familia(tipo)
                existente = next(
                    (
                        c
                        for c in self._compromissos
                        if c.tipo == "ri"
                        and c.product_code == r["ProductCode"]
                        and c.familia == familia
                        and c.operacao == r["Operation"]
                    ),
                    None,
                )
                if existente:
                    existente.taxa_mensal += _d(r["TotalCost"])
                    continue
                servico = "rds" if r["ProductCode"] == "AmazonRDS" else "elasticache"
                self._compromissos.append(
                    Compromisso(
                        tipo="ri",
                        arn=(
                            f"arn:aws:{servico}:us-east-1:{PAGADOR[0]}:ri:"
                            f"{_hash(ut, r['Operation'])[:12]}"
                        ),
                        usage_type=ut,
                        product_code=r["ProductCode"],
                        taxa_mensal=_d(r["TotalCost"]),
                        familia=familia,
                        operacao=r["Operation"],
                    )
                )

    def _fechar_compromissos(self) -> None:
        """Rateia cada compromisso entre as horas que ele cobriu.

        Savings Plan: 95% usado; o ocioso vai para a `SavingsPlanRecurringFee`.
        Reserva: usada até o desconto mínimo de 20% sobre o sob demanda; o que
        passar disso é reserva ociosa (vai para a `RIFee`). Sem esse teto, uma
        reserva maior que o uso daria custo efetivo ACIMA do sob demanda — o
        que nenhuma reserva real faz.
        """
        for c in self._compromissos:
            sob_demanda = sum((Decimal(linha["_sob_demanda"]) for linha in c.cobertas), _ZERO)
            teto = sob_demanda * Decimal("0.80")
            alvo = c.taxa_mensal * (Decimal("0.95") if c.tipo == "sp" else Decimal("1"))
            c.usado = _q(min(alvo, teto))
            efetivos = repartir(c.usado, [Decimal(linha["_sob_demanda"]) for linha in c.cobertas])
            for linha, efetivo in zip(c.cobertas, efetivos, strict=True):
                horas = _d(linha["line_item_usage_amount"])
                if c.tipo == "sp":
                    linha.update(
                        {
                            "savings_plan_savings_plan_a_r_n": c.arn,
                            "savings_plan_savings_plan_effective_cost": _fmt(efetivo),
                            "savings_plan_savings_plan_rate": _fmt(efetivo / horas),
                            "savings_plan_offering_type": c.oferta,
                            "savings_plan_payment_option": "No Upfront",
                            "savings_plan_purchase_term": c.prazo,
                        }
                    )
                else:
                    linha.update(
                        {
                            "reservation_reservation_a_r_n": c.arn,
                            "reservation_effective_cost": _fmt(efetivo),
                            "reservation_recurring_fee_for_usage": _fmt(efetivo),
                            "reservation_amortized_upfront_cost_for_usage": "0",
                        }
                    )
            if c.tipo == "sp":
                self._linhas_do_sp(c)
            else:
                self._linha_rifee(c)

    def _linhas_do_sp(self, c: Compromisso) -> None:
        taxas = repartir(c.taxa_mensal, [Decimal("1")] * DIAS)
        usados = repartir(c.usado, [Decimal("1")] * DIAS)
        for k in range(DIAS):
            dia = INICIO + timedelta(days=k)
            self._linha(
                product_code="ComputeSavingsPlans",
                usage_type=c.usage_type,
                operacao="",
                tipo_linha="SavingsPlanRecurringFee",
                custo=taxas[k],
                quantidade=Decimal("24"),
                inicio=dia,
                fim=dia + timedelta(days=1),
                line_item_line_item_description=(f"{c.prazo} No Upfront {c.oferta}"),
                savings_plan_savings_plan_a_r_n=c.arn,
                savings_plan_offering_type=c.oferta,
                savings_plan_payment_option="No Upfront",
                savings_plan_purchase_term=c.prazo,
                savings_plan_recurring_commitment_for_billing_period=_fmt(c.taxa_mensal),
                savings_plan_amortized_upfront_commitment_for_billing_period="0",
                savings_plan_total_commitment_to_date=_fmt(taxas[k]),
                savings_plan_used_commitment=_fmt(usados[k]),
                pricing_unit="Hrs",
                product_product_family="Savings Plan",
            )
        # Negação: anula o sob demanda das horas cobertas, agrupada por
        # (tipo de uso, região, dia), como a AWS faz.
        grupos: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for linha in c.cobertas:
            grupos[
                (
                    linha["line_item_usage_type"],
                    linha["product_region_code"],
                    linha["line_item_usage_start_date"],
                )
            ].append(linha)
        for (ut, regiao, inicio_iso), linhas in sorted(grupos.items()):
            inicio = datetime.strptime(inicio_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            self._linha(
                product_code=linhas[0]["line_item_product_code"],
                usage_type=ut,
                operacao=linhas[0]["line_item_operation"],
                tipo_linha="SavingsPlanNegation",
                custo=-sum((_d(x["line_item_unblended_cost"]) for x in linhas), _ZERO),
                quantidade=-sum((_d(x["line_item_usage_amount"]) for x in linhas), _ZERO),
                inicio=inicio,
                fim=inicio + timedelta(days=1),
                line_item_line_item_description=(
                    "SavingsPlanNegation used by AccountId : "
                    + linhas[0]["line_item_usage_account_id"]
                ),
                savings_plan_savings_plan_a_r_n=c.arn,
                product_region_code=regiao,
            )

    def _linha_rifee(self, c: Compromisso) -> None:
        ociosa = c.taxa_mensal - c.usado
        self._linha(
            product_code=c.product_code,
            usage_type=c.usage_type,
            operacao=c.operacao,
            tipo_linha="RIFee",
            custo=c.taxa_mensal,
            quantidade=sum((_d(x["line_item_usage_amount"]) for x in c.cobertas), _ZERO),
            inicio=INICIO,
            fim=FIM,
            line_item_line_item_description=f"Reserved {c.familia} — recurring fee",
            reservation_reservation_a_r_n=c.arn,
            reservation_amortized_upfront_fee_for_billing_period="0",
            reservation_unused_amortized_upfront_fee_for_billing_period="0",
            reservation_unused_recurring_fee=_fmt(ociosa),
            reservation_unused_quantity="0",
            pricing_term="Reserved",
        )

    # ── recursos que não são instância ──────────────────────────────────

    def _por_recurso(
        self,
        origem: dict[str, str],
        prefixo: str,
        n: int,
        produto: dict | None = None,
    ) -> None:
        """Divide a linha do bill em N recursos iguais, por dia."""
        usage_type = origem["UsageType"]
        regiao = _regiao(usage_type)
        recursos = []
        for i in range(n):
            recurso = self._novo_recurso(prefixo, usage_type, i, regiao, produto=produto)
            recurso["horas"] = HORAS_MES
            recursos.append(recurso)
            self._recursos[(origem["ProductCode"], regiao)].append(recurso)
        self._diario(origem, recursos, [Decimal("1")] * n)

    def _diario(
        self,
        origem: dict[str, str],
        recursos: list[dict | None],
        pesos: list[Decimal],
        *,
        mensal: bool = False,
    ) -> None:
        """Espalha quantidade e custo da linha real por recurso e por dia.

        ``mensal`` emite uma linha por recurso cobrindo o mês. Serve às linhas
        auxiliares (storage de banco, requisição, transferência): diárias, elas
        multiplicavam o arquivo por 30 sem exercitar nada que as instâncias
        diárias já não exercitem.
        """
        quantidade = _d(origem["UsageQuantity"])
        custo = _d(origem["TotalCost"])
        janelas = (
            [(INICIO, FIM)]
            if mensal
            else [(INICIO + timedelta(days=d), INICIO + timedelta(days=d + 1)) for d in range(DIAS)]
        )
        pesos_dia = [w for w in pesos for _ in janelas]
        custos = repartir(custo, pesos_dia)
        quantidades = repartir(quantidade, pesos_dia)
        k = 0
        for recurso in recursos:
            for inicio, fim in janelas:
                self._linha(
                    product_code=origem["ProductCode"],
                    usage_type=origem["UsageType"],
                    operacao=origem["Operation"],
                    tipo_linha="Usage",
                    custo=custos[k],
                    quantidade=quantidades[k],
                    inicio=inicio,
                    fim=fim,
                    recurso=recurso,
                    line_item_line_item_description=origem["ItemDescription"]
                    or f"{origem['ProductCode']} {origem['UsageType']}",
                    pricing_public_on_demand_cost=_fmt(custos[k]),
                )
                k += 1

    def _anexar(self, origem: dict[str, str], candidatos: list[dict]) -> None:
        """Linha auxiliar (IOPS, backup, storage do banco) vai para os recursos
        que a geraram, na proporção das horas deles. Sem candidato, fica sem
        recurso — como transferência e requisição no CUR real.

        No máximo 5 recursos (os de mais horas): o custo se conserva do mesmo
        jeito, e o arquivo não explode em linhas de centavo.
        """
        if not candidatos:
            self._diario(origem, [None], [Decimal("1")], mensal=True)
            return
        escolhidos = sorted(candidatos, key=lambda r: (-r.get("horas", HORAS_MES), r["id"]))[:5]
        self._diario(
            origem, escolhidos, [r.get("horas", HORAS_MES) for r in escolhidos], mensal=True
        )

    # ── orquestração ────────────────────────────────────────────────────

    def gerar(self) -> ResumoAWS:
        origem = self._ler_origem()
        self._registrar_compromissos(origem)

        auxiliares: list[dict[str, str]] = []
        impostos: list[dict[str, str]] = []
        for r in origem:
            ut, pc = r["UsageType"], r["ProductCode"]
            if pc == "Tax":
                impostos.append(r)
                continue
            if pc == "Savings Plans for AWS Compute usage" or "HeavyUsage:" in ut:
                continue  # viram SavingsPlanRecurringFee / RIFee
            m = _INSTANCIA.search(ut)
            # `ExtendedSupport…-NodeUsage:cache.x` é taxa sobre um nó que já
            # existe, não um nó novo: vira linha anexada ao nó.
            if m and "ExtendedSupport" not in ut:
                self._instancias(r, m.group("tipo"))
                continue
            auxiliares.append(r)

        for r in auxiliares:
            self._auxiliar(r)

        self._fechar_compromissos()
        self._repartir_custo_sob_demanda()
        self._impostos(impostos)

        total = sum((_d(r["TotalCost"]) for r in origem), _ZERO)
        imposto = sum((_d(r["TotalCost"]) for r in impostos), _ZERO)
        instancias = sum(1 for lista in self._recursos.values() for x in lista if x.get("tipo"))
        return ResumoAWS(
            total_real=total,
            imposto_real=imposto,
            linhas=len(self._linhas),
            instancias=instancias,
            compromissos={
                c.arn: {
                    "tipo": c.tipo,
                    "taxa_mensal": _fmt(c.taxa_mensal),
                    "usado": _fmt(c.usado),
                    "sob_demanda_coberto": _fmt(
                        sum((Decimal(x["_sob_demanda"]) for x in c.cobertas), _ZERO)
                    ),
                }
                for c in self._compromissos
            },
        )

    def _auxiliar(self, r: dict[str, str]) -> None:  # noqa: PLR0911 - despacho por tipo
        ut, pc = r["UsageType"], r["ProductCode"]
        regiao = _regiao(ut)
        quantidade = _d(r["UsageQuantity"])
        mesma_regiao = self._recursos.get((pc, regiao), [])

        m_hora = _POR_HORA_RECURSO.search(ut)
        if m_hora and quantidade > 0:
            n = max(1, round(quantidade / HORAS_MES))
            self._por_recurso(r, _PREFIXO_RECURSO.get(m_hora.group(1).split(".")[0], "res"), n)
            return
        if "LCUUsage" in ut:
            self._anexar(r, [x for x in mesma_regiao if x["id"].startswith("lb-")])
            return
        if "VolumeUsage" in ut:
            volume = ut.rsplit(".", 1)[-1]
            n = max(1, math.ceil(quantidade / 500))
            self._por_recurso(
                r, "vol", n, {"volume_api_name": volume, "storage_media": "SSD-backed"}
            )
            return
        if "SnapshotUsage" in ut:
            self._por_recurso(r, "snap", max(1, math.ceil(quantidade / 1000)))
            return
        if "TimedStorage" in ut and "ByteHrs" in ut:
            classe = "Standard" if ut.endswith("TimedStorage-ByteHrs") else ut.split("-")[-2]
            n = max(1, math.ceil(quantidade / 20000))
            self._por_bucket(r, n, classe)
            return
        if "VolumeP-" in ut:
            self._anexar(r, [x for x in mesma_regiao if x["id"].startswith("vol-")])
            return
        if "NatGateway-Bytes" in ut:
            self._anexar(r, [x for x in mesma_regiao if x["id"].startswith("nat-")])
            return
        if pc == "AmazonS3":
            self._anexar(r, [x for x in mesma_regiao if x.get("bucket")])
            return
        if pc == "AmazonRDS":
            self._anexar(
                r,
                [
                    x
                    for x in mesma_regiao
                    if x.get("tipo")
                    and x["produto"].get("database_engine")
                    == _MOTOR_RDS.get(r["Operation"], ("?",))[0]
                ],
            )
            return
        m = re.search(r"(EBSOptimized|CPUCredits|ExtendedSupport[^:]*-NodeUsage):([\w.\-]+)$", ut)
        if m:
            alvo = _tipo_normalizado(m.group(2))
            self._anexar(
                r,
                [
                    x
                    for x in mesma_regiao
                    if x.get("tipo") and (x["tipo"] == alvo or x["tipo"].startswith(alvo + "."))
                ],
            )
            return
        self._diario(r, [None], [Decimal("1")], mensal=True)

    def _por_bucket(self, r: dict[str, str], n: int, classe: str) -> None:
        regiao = _regiao(r["UsageType"])
        recursos = []
        for i in range(n):
            recurso = self._novo_recurso(
                "bucket", r["UsageType"], i, regiao, produto={"storage_class": classe}
            )
            recurso["id"] = (
                f"{recurso['tags']['user_app']}-{classe.lower()}-{_hash(r['UsageType'], i)[:8]}"
            )
            recurso["bucket"] = True
            recurso["horas"] = HORAS_MES
            recursos.append(recurso)
            self._recursos[("AmazonS3", regiao)].append(recurso)
        self._diario(r, recursos, [Decimal("1")] * n)

    def _repartir_custo_sob_demanda(self) -> None:
        """Custo real de cada linha do bill → linhas `Usage` das instâncias.

        Repartido na proporção das horas sob demanda, com o resto do
        arredondamento na maior parte: a soma volta exata ao bill real.
        """
        por_origem: dict[str, list[dict]] = defaultdict(list)
        for linha in self._linhas:
            if "_peso_sd" in linha:
                por_origem[linha["_origem"]].append(linha)
        custos_origem: dict[str, Decimal] = {}
        for lista in self._recursos.values():
            for recurso in lista:
                for ut, custo in recurso.get("_origens", []):
                    custos_origem[ut] = custo
        for ut, linhas in por_origem.items():
            custo = custos_origem.get(ut, _ZERO)
            partes = repartir(custo, [Decimal(x["_peso_sd"]) for x in linhas])
            for linha, parte in zip(linhas, partes, strict=True):
                linha["line_item_unblended_cost"] = _fmt(parte)
                horas = _d(linha["line_item_usage_amount"])
                linha["line_item_unblended_rate"] = _fmt(parte / horas) if horas else "0"

    def _impostos(self, impostos: list[dict[str, str]]) -> None:
        """Imposto do bill real, repartido por produto na proporção do custo."""
        if not impostos:
            return
        total = sum((_d(r["TotalCost"]) for r in impostos), _ZERO)
        custo_por_produto: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        for linha in self._linhas:
            if linha["line_item_line_item_type"] in ("Usage", "SavingsPlanRecurringFee", "RIFee"):
                custo_por_produto[linha["line_item_product_code"]] += _d(
                    linha["line_item_unblended_cost"]
                )
        produtos = sorted(p for p, v in custo_por_produto.items() if v > 0)
        partes = repartir(total, [custo_por_produto[p] for p in produtos])
        for produto, parte in zip(produtos, partes, strict=True):
            self._linha(
                product_code=produto,
                usage_type="",
                operacao="",
                tipo_linha="Tax",
                custo=parte,
                quantidade=_ZERO,
                inicio=INICIO,
                fim=FIM,
                line_item_line_item_description=f"Tax for product code {produto}",
                line_item_tax_type="VAT",
                pricing_unit="",
                product_product_family="",
            )

    # ── saída ───────────────────────────────────────────────────────────

    def escrever(self, destino: Path) -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO()
        escritor = csv.DictWriter(
            buffer, fieldnames=_COLUNAS_CUR2, extrasaction="ignore", lineterminator="\n"
        )
        escritor.writeheader()
        ordenadas = sorted(
            self._linhas,
            key=lambda x: (
                x["line_item_usage_start_date"],
                x["line_item_product_code"],
                x["line_item_usage_type"],
                x["line_item_resource_id"],
                x["line_item_line_item_type"],
                x["identity_line_item_id"],
            ),
        )
        escritor.writerows(ordenadas)
        # mtime fixo: sem ele o cabeçalho do gzip muda a cada execução e o
        # arquivo deixa de ser byte a byte reprodutível.
        with (
            destino.open("wb") as bruto,
            gzip.GzipFile(fileobj=bruto, mode="wb", mtime=0, filename="") as gz,
        ):
            gz.write(buffer.getvalue().encode("utf-8"))


def gerar_cur_aws(origem: Path, destino: Path) -> ResumoAWS:
    gerador = GeradorCUR(origem)
    resumo = gerador.gerar()
    gerador.escrever(destino)
    return resumo


# ── GCP: export detalhado do BigQuery ───────────────────────────────────────

GCP_INICIO = datetime(2025, 12, 1, tzinfo=UTC)
GCP_DIAS = 31
# Taxa USD→BRL do export (`currency_conversion_rate`). Inventada e declarada:
# só aparece na coluna, nenhuma conta do pipeline usa este valor.
GCP_TAXA_BRL = Decimal("5.4312")

# Localidade do SKU → região. "Americas" é multi-região no preço; o recurso
# vive numa região concreta, e us-central1 é a mais comum.
_REGIAO_GCP = {
    "Americas": "us-central1",
    "Sao Paulo": "southamerica-east1",
    "Dallas": "us-south1",
    "Los Angeles": "us-west2",
    "US": "us-central1",
}
_SKU_COMPUTE = re.compile(
    r"^(?P<familia>E2|N2D|N2|N1 Predefined|C2|C3|T2D) Instance (?P<recurso>Core|Ram)"
    r" running in (?P<local>.+)$"
)
_SKU_DISCO = re.compile(r"PD Capacity(?: in (?P<local>.+))?$")
_TIPO_CREDITO = {
    "SPENDING_BASED_DISCOUNT": ("DISCOUNT", "Spending based discount"),
    "SUSTAINED_USAGE_DISCOUNT": ("SUSTAINED_USAGE_DISCOUNT", "Sustained usage discount"),
}
_PREFIXO_SERIE = {
    "E2": "e2",
    "N2": "n2",
    "N2D": "n2d",
    "N1 Predefined": "n1",
    "C2": "c2",
    "C3": "c3",
    "T2D": "t2d",
}
# GiB por vCPU de cada série nomeada. Fora destas razões, o tipo sai `custom`,
# que é o que o GCP faz quando a VM não é de um tamanho predefinido. N1 tem
# razões próprias (standard = 3,75): com a tabela das séries novas, toda N1
# saía `custom`.
_SERIES = (("highcpu", Decimal("1")), ("standard", Decimal("4")), ("highmem", Decimal("8")))
_SERIES_N1 = (
    ("highcpu", Decimal("0.9")),
    ("standard", Decimal("3.75")),
    ("highmem", Decimal("6.5")),
)


@dataclass
class ResumoGCP:
    total_real: Decimal
    linhas: int
    vms: int
    discos: int


def _data(texto: str, padrao: datetime) -> datetime:
    return datetime.strptime(texto, "%Y-%m-%d").replace(tzinfo=UTC) if texto else padrao


def _dias(inicio: datetime, fim: datetime) -> list[datetime]:
    n = max(1, (fim - inicio).days + 1)
    return [inicio + timedelta(days=k) for k in range(n)]


class GeradorBigQuery:
    """Cost Table (projeto, SKU, intervalo) → export detalhado, diário."""

    def __init__(self, origem: Path) -> None:
        self._origem = origem
        self._linhas: list[dict] = []
        self._vms: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._discos = 0

    def _ler(self) -> list[dict[str, str]]:
        with self._origem.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh, delimiter=";"))

    def gerar(self) -> ResumoGCP:
        origem = self._ler()
        creditos: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
        usos: list[dict[str, str]] = []
        outros: list[dict[str, str]] = []
        for r in origem:
            if r["Cost type"] == "Total":
                continue  # linha-resumo do Cost Table: não existe no export
            if r["Credit type"]:
                chave = (
                    r["Project ID"],
                    r["SKU description"],
                    r["Usage start date"],
                    r["Usage end date"],
                )
                creditos[chave].append(r)
            elif r["Cost type"] == "Usage":
                usos.append(r)
            else:
                outros.append(r)

        # Compute primeiro: as VMs precisam existir antes da licença, do IP
        # externo e do disco que se anexam a elas.
        usos.sort(
            key=lambda r: (
                _SKU_COMPUTE.match(r["SKU description"]) is None,
                r["Project ID"],
                r["SKU description"],
                r["Usage start date"],
            )
        )
        pares = self._pares_core_ram(usos)
        # No Cost Table o crédito nem sempre tem o mesmo intervalo de datas do
        # uso (o SUD do N2 cobre 19 a 31/12, o uso 18 a 31/12). Casa por intervalo
        # exato; senão, pelo uso de maior custo do mesmo (projeto, SKU).
        por_sku: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in usos:
            por_sku[(r["Project ID"], r["SKU description"])].append(r)
        anexos: dict[int, list[dict[str, str]]] = defaultdict(list)
        orfaos: list[list[dict[str, str]]] = []
        for (proj, sku, ini, fim), lista in sorted(creditos.items()):
            candidatos = por_sku.get((proj, sku), [])
            exato = [
                u for u in candidatos if (u["Usage start date"], u["Usage end date"]) == (ini, fim)
            ]
            alvo = exato or sorted(candidatos, key=lambda u: -_dm(u["Unrounded Cost (R$)"]))
            if alvo:
                anexos[id(alvo[0])].extend(lista)
            else:
                orfaos.append(lista)
        for r in usos:
            self._uso(r, anexos.get(id(r), []), pares)
        # Crédito sem uso nenhum do mesmo SKU: vira linha de custo zero
        # carregando o crédito, sem recurso, para o valor não sumir.
        for lista in orfaos:
            base = dict(lista[0])
            base.update(
                {
                    "Credit type": "",
                    "Unrounded Cost (R$)": "0",
                    "Usage amount": "0",
                    "SKU description": lista[0]["SKU description"],
                }
            )
            self._uso(base, lista, pares, sem_recurso=True)
        for r in outros:
            self._nao_uso(r)

        total = sum(
            (_dm(r["Unrounded Cost (R$)"]) for r in origem if r["Cost type"] != "Total"), _ZERO
        )
        return ResumoGCP(
            total_real=total,
            linhas=len(self._linhas),
            vms=sum(len(v) for v in self._vms.values()),
            discos=self._discos,
        )

    # ── compute ─────────────────────────────────────────────────────────

    def _pares_core_ram(self, usos: list[dict[str, str]]) -> dict[tuple, dict]:
        """Agrupa core e RAM do mesmo (projeto, família, local, intervalo).

        A forma da VM sai da razão RAM/vCPU **real** do bill; o número de VMs,
        de quantas precisam existir para caber no máximo 24h/dia por VM.
        """
        grupos: dict[tuple, dict] = {}
        for r in usos:
            m = _SKU_COMPUTE.match(r["SKU description"])
            if not m:
                continue
            chave = (
                r["Project ID"],
                m.group("familia"),
                m.group("local"),
                r["Usage start date"],
                r["Usage end date"],
            )
            g = grupos.setdefault(chave, {"core": _ZERO, "ram": _ZERO})
            g["core" if m.group("recurso") == "Core" else "ram"] += _dm(r["Usage amount"])
        for chave, g in grupos.items():
            projeto, familia, local, ini, fim = chave
            dias = len(_dias(_data(ini, GCP_INICIO), _data(fim, GCP_INICIO)))
            core, ram = g["core"], g["ram"]
            vcpu_medio = core / (24 * dias) if core else _ZERO
            razao = (ram / core) if core else Decimal("4")
            k = next((c for c in (16, 8, 4, 2) if Decimal(c) <= vcpu_medio), 2)
            n = max(1, math.ceil(core / (Decimal(k) * 24 * dias))) if core else 1
            series = _SERIES_N1 if familia == "N1 Predefined" else _SERIES
            serie = next(
                (nome for nome, gib in series if abs(razao - gib) <= gib * Decimal("0.05")), None
            )
            prefixo = _PREFIXO_SERIE[familia]
            memoria_mb = int((Decimal(k) * razao * 1024).to_integral_value())
            tipo = (
                f"{prefixo}-{serie}-{k}"
                if serie
                else f"{prefixo}-custom-{k}-{memoria_mb - memoria_mb % 256}"
            )
            g.update({"k": k, "n": n, "tipo": tipo, "memoria_mb": memoria_mb, "dias": dias})
            regiao = _REGIAO_GCP.get(local, "us-central1")
            vms = []
            restante = core
            for i in range(n):
                horas_vm = min(Decimal(k) * 24 * dias, restante) / Decimal(k)
                restante -= horas_vm * k
                vm_id = str(int(_hash(projeto, familia, local, i)[:15], 16))
                zona = regiao + "-" + _escolha(["a", "b", "c"], projeto, familia, i)
                vms.append(
                    {
                        "nome": f"vm-{projeto}-{prefixo}-{i:02d}",
                        "global": (
                            f"//compute.googleapis.com/projects/{projeto}/zones/{zona}"
                            f"/instances/{vm_id}"
                        ),
                        "zona": zona,
                        "regiao": regiao,
                        "horas": horas_vm,
                        "tipo": tipo,
                        "cores": k,
                        "memoria_mb": memoria_mb,
                        "app": _escolha(APPS, projeto, familia, i),
                        "env": _escolha(["prod", "prod", "staging", "dev"], projeto, i),
                    }
                )
            g["vms"] = vms
            self._vms[(projeto, regiao)].extend(vms)
        return grupos

    # ── linhas ──────────────────────────────────────────────────────────

    def _base(self, r: dict[str, str], dia: datetime, custo: Decimal) -> dict:
        local = _SKU_COMPUTE.match(r["SKU description"]) or _SKU_DISCO.search(r["SKU description"])
        local_txt = local.group("local") if local and local.group("local") else None
        regiao = _REGIAO_GCP.get(local_txt or "")
        return {
            "billing_account_id": r["Billing account ID"] or "000000-000000-000000",
            "service": {"id": r["Service ID"], "description": r["Service description"]},
            "sku": {"id": r["SKU ID"], "description": r["SKU description"]},
            "usage_start_time": _iso(dia),
            "usage_end_time": _iso(dia + timedelta(days=1)),
            "project": {
                "id": r["Project ID"],
                "number": str(int(_hash("proj", r["Project ID"])[:11], 16)),
                "name": r["Project name"],
                "labels": [],
                "ancestry_numbers": "/",
            },
            "labels": [],
            "system_labels": [],
            "location": {
                "location": regiao or "global",
                "country": "BR" if regiao == "southamerica-east1" else ("US" if regiao else None),
                "region": regiao,
                "zone": None,
            },
            "resource": {"name": None, "global_name": None},
            "tags": [],
            "transaction_type": "GOOGLE",
            "seller_name": None,
            "export_time": _iso(dia + timedelta(days=1, hours=6)),
            "cost": float(0),  # substituído abaixo por string decimal
            "currency": "BRL",
            "currency_conversion_rate": str(GCP_TAXA_BRL),
            "cost_at_list": None,
            "usage": {
                "amount": "0",
                "unit": r["Usage unit"] or None,
                "amount_in_pricing_units": "0",
                "pricing_unit": r["Usage unit"] or None,
            },
            "credits": [],
            "invoice": {"month": "202512", "publisher_type": "GOOGLE"},
            "cost_type": "regular",
            "adjustment_info": None,
            "_custo": custo,
        }

    def _uso(
        self,
        r: dict[str, str],
        creditos: list[dict[str, str]],
        pares: dict,
        *,
        sem_recurso: bool = False,
    ) -> None:
        dias = _dias(
            _data(r["Usage start date"], GCP_INICIO), _data(r["Usage end date"], GCP_INICIO)
        )
        custo = _dm(r["Unrounded Cost (R$)"])
        quantidade = _dm(r["Usage amount"])
        m = _SKU_COMPUTE.match(r["SKU description"])
        alvos: list[dict | None] = [None]
        pesos = [Decimal("1")]
        if sem_recurso:
            pass
        elif m:
            chave = (
                r["Project ID"],
                m.group("familia"),
                m.group("local"),
                r["Usage start date"],
                r["Usage end date"],
            )
            alvos = pares[chave]["vms"]
            pesos = [vm["horas"] for vm in alvos]
        elif (
            r["SKU description"].startswith("Licensing Fee")
            or "External IP" in r["SKU description"]
        ):
            vms = [
                vm
                for lista in self._vms.values()
                for vm in lista
                if vm["global"].split("/projects/")[1].startswith(r["Project ID"] + "/")
            ]
            if vms:
                alvos, pesos = vms[:5], [vm["horas"] for vm in vms[:5]]
        elif _SKU_DISCO.search(r["SKU description"]) and quantidade > 0:
            tamanho = quantidade * Decimal(GCP_DIAS) / Decimal(len(dias))
            n = max(1, math.ceil(tamanho / 200))
            self._discos += n
            local = _SKU_DISCO.search(r["SKU description"]).group("local") or "Americas"
            regiao = _REGIAO_GCP.get(local, "us-central1")
            alvos = [
                {
                    "nome": f"disk-{r['Project ID']}-{_hash(r['SKU description'], i)[:6]}",
                    "global": (
                        f"//compute.googleapis.com/projects/{r['Project ID']}/zones/"
                        f"{regiao}-a/disks/{int(_hash('d', r['Project ID'], i)[:15], 16)}"
                    ),
                    "zona": f"{regiao}-a",
                    "regiao": regiao,
                    "horas": Decimal(24 * len(dias)),
                    "app": _escolha(APPS, r["Project ID"], i),
                    "env": "prod",
                }
                for i in range(n)
            ]
            pesos = [Decimal("1")] * n

        pesos_dia = [w for w in pesos for _ in dias]
        custos = repartir(custo, pesos_dia)
        quantidades = repartir(quantidade, pesos_dia)
        creditos_rep = [(c, repartir(_dm(c["Unrounded Cost (R$)"]), pesos_dia)) for c in creditos]
        k = 0
        for alvo in alvos:
            for dia in dias:
                linha = self._base(r, dia, custos[k])
                linha["cost_at_list"] = str(_q(custos[k]))
                self._unidades(linha, r, quantidades[k], m, alvo)
                for c, partes in creditos_rep:
                    tipo, nome = _TIPO_CREDITO.get(c["Credit type"], ("DISCOUNT", c["Credit type"]))
                    linha["credits"].append(
                        {
                            "name": nome,
                            "amount": str(_q(partes[k])),
                            "full_name": nome,
                            "id": c["Credit type"],
                            "type": tipo,
                        }
                    )
                if alvo is not None:
                    linha["resource"] = {"name": alvo["nome"], "global_name": alvo["global"]}
                    linha["location"].update(
                        {"region": alvo["regiao"], "zone": alvo["zona"], "location": alvo["zona"]}
                    )
                    linha["labels"] = [
                        {"key": "app", "value": alvo["app"]},
                        {"key": "env", "value": alvo["env"]},
                    ]
                    if "tipo" in alvo:
                        linha["system_labels"] = [
                            {"key": "compute.googleapis.com/machine_spec", "value": alvo["tipo"]},
                            {"key": "compute.googleapis.com/cores", "value": str(alvo["cores"])},
                            {
                                "key": "compute.googleapis.com/memory",
                                "value": str(alvo["memoria_mb"]),
                            },
                        ]
                self._linhas.append(linha)
                k += 1

    @staticmethod
    def _unidades(linha: dict, r: dict[str, str], quantidade: Decimal, m, alvo) -> None:
        """Unidade base (segundos, byte-segundos) + unidade de preço.

        É exatamente a armadilha do export real: `usage.amount` vem na unidade
        base e a quantidade útil está em `amount_in_pricing_units`.
        """
        unidade = r["Usage unit"]
        base, fator = {
            "hour": ("seconds", Decimal("3600")),
            "gibibyte hour": ("byte-seconds", Decimal("3865470566400")),  # 2^30 * 3600
            "gibibyte month": ("byte-seconds", Decimal(2**30) * 3600 * 24 * GCP_DIAS),
            "gibibyte": ("bytes", Decimal(2**30)),
        }.get(unidade, (unidade or None, Decimal("1")))
        linha["usage"] = {
            "amount": str(_q(quantidade * fator)),
            "unit": base,
            "amount_in_pricing_units": str(_q(quantidade)),
            "pricing_unit": unidade or None,
        }

    def _nao_uso(self, r: dict[str, str]) -> None:
        dia = _data(r["Usage start date"], GCP_INICIO + timedelta(days=GCP_DIAS - 1))
        linha = self._base(r, dia, _dm(r["Unrounded Cost (R$)"]))
        linha["cost_type"] = {"Tax": "tax", "Rounding error": "rounding error"}.get(
            r["Cost type"], r["Cost type"].lower()
        )
        if linha["cost_type"] == "tax":
            linha["service"] = {"id": "", "description": "Invoice"}
            linha["sku"] = {"id": "", "description": "Tax"}
        linha["usage"] = {
            "amount": "0",
            "unit": None,
            "amount_in_pricing_units": "0",
            "pricing_unit": None,
        }
        self._linhas.append(linha)

    # ── saída ───────────────────────────────────────────────────────────

    def _finais(self) -> list[dict]:
        saida = []
        for linha in sorted(
            self._linhas,
            key=lambda x: (
                x["usage_start_time"],
                x["project"]["id"],
                x["sku"]["description"],
                x["resource"]["name"] or "",
                x["cost_type"],
            ),
        ):
            final = {k: v for k, v in linha.items() if not k.startswith("_")}
            # Custo como string decimal: é como o `bq extract` em JSON entrega
            # NUMERIC, e evita que o próprio mock carregue erro de float.
            final["cost"] = str(_q(linha["_custo"]))
            saida.append(final)
        return saida

    def escrever_ndjson(self, destino: Path) -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        texto = "".join(
            json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in self._finais()
        )
        with (
            destino.open("wb") as bruto,
            gzip.GzipFile(fileobj=bruto, mode="wb", mtime=0, filename="") as gz,
        ):
            gz.write(texto.encode("utf-8"))

    def escrever_csv_achatado(self, destino: Path, limite: int = 400) -> None:
        """Variante CSV como sai de uma consulta no console: registros
        aninhados viram `service.description`, e os campos repetidos (créditos,
        labels) viram JSON numa coluna. Amostra das primeiras linhas: o objetivo
        é o formato, não o volume."""
        colunas = [
            "billing_account_id",
            "service.id",
            "service.description",
            "sku.id",
            "sku.description",
            "usage_start_time",
            "usage_end_time",
            "project.id",
            "project.name",
            "labels",
            "system_labels",
            "location.location",
            "location.region",
            "location.zone",
            "resource.name",
            "resource.global_name",
            "cost",
            "currency",
            "currency_conversion_rate",
            "cost_at_list",
            "usage.amount",
            "usage.unit",
            "usage.amount_in_pricing_units",
            "usage.pricing_unit",
            "credits",
            "invoice.month",
            "cost_type",
        ]
        linhas = []
        for x in self._finais()[:limite]:
            plano = {}
            for col in colunas:
                if "." in col:
                    pai, filho = col.split(".", 1)
                    valor = (x.get(pai) or {}).get(filho)
                else:
                    valor = x.get(col)
                if isinstance(valor, list):
                    valor = json.dumps(valor, ensure_ascii=False)
                plano[col] = "" if valor is None else valor
            linhas.append(plano)
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=colunas, lineterminator="\n")
            w.writeheader()
            w.writerows(linhas)


def gerar_bigquery_gcp(origem: Path, destino_ndjson: Path, destino_csv: Path) -> ResumoGCP:
    gerador = GeradorBigQuery(origem)
    resumo = gerador.gerar()
    gerador.escrever_ndjson(destino_ndjson)
    gerador.escrever_csv_achatado(destino_csv)
    return resumo


# ── entrada de linha de comando ─────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    args = p.parse_args()

    resumo = gerar_cur_aws(
        FIXTURES / "aws_cost_explorer_full.csv",
        args.destino / "mock_aws_cur2_2026-03.csv.gz",
    )
    print(  # noqa: T201
        f"AWS CUR 2.0: {resumo.linhas:,} linhas, {resumo.instancias} instâncias, "
        f"bill real US$ {resumo.total_real:,.2f} (imposto {resumo.imposto_real:,.2f})"
    )
    for arn, c in resumo.compromissos.items():
        print(  # noqa: T201
            f"  {c['tipo']:>2} {arn[-12:]}  taxa {c['taxa_mensal']:>12}  "
            f"usado {c['usado']:>12}  sob demanda coberto {c['sob_demanda_coberto']:>12}"
        )

    gcp = gerar_bigquery_gcp(
        FIXTURES / "gcp_cost_table_full.csv",
        args.destino / "mock_gcp_bigquery_2025-12.json.gz",
        args.destino / "mock_gcp_bigquery_achatado_2025-12.csv",
    )
    print(  # noqa: T201
        f"GCP BigQuery: {gcp.linhas:,} linhas, {gcp.vms} VMs, {gcp.discos} discos, "
        f"bill real R$ {gcp.total_real:,.2f}"
    )


if __name__ == "__main__":
    main()
