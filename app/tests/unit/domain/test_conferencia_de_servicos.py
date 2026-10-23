"""PLANO_CONFERENCIA_DE_SERVICOS: o que cada serviço vira na análise.

Os nomes de medidor abaixo são os dos catálogos oficiais (Price List da AWS, Retail
Prices da Azure, Cloud Billing Catalog do GCP, cetools da OCI), conferidos em 23/10.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.categoria_por_uso import (
    RECONHECIDO_SEM_COTACAO,
    SERVERLESS,
    SERVERLESS_MEMORIA,
    SERVERLESS_VCPU,
    quantidade_por_uso,
    tipo_por_uso,
)
from app.domain.services.reclassificacao_por_uso import reclassificar
from app.domain.services.service_identity import variantes_de_marca
from app.domain.services.sku_matcher import SkuMatcher
from app.infrastructure.database.seed_classification_rules import (
    AWS_RULES,
    GCP_RULES,
    OCI_RULES,
)
from app.services.billing_aggregator import BillingAggregator


def _matcher(provider: str, seed: list) -> SkuMatcher:
    regras = [
        SkuClassificationRule(
            provider=provider,
            pattern=padrao,
            kind=tipo,
            component_type=componente,
            family=familia,
            confidence=confianca,
            priority=prioridade,
        )
        for (padrao, tipo, componente, familia, confianca, prioridade) in seed
    ]
    return SkuMatcher([], regras)


# ── C1: volume de dados não é memória ───────────────────────────────────────


@pytest.mark.parametrize(
    ("uso", "unidade", "esperado"),
    [
        # Transferência entre regiões: era RAM pela L3 e entrava no pool de máquinas
        ("USE1-USW2-AWS-Out-ABytes-T2", "gibibyte", "network_egress"),
        ("USE1-USW2-AWS-In-ABytes-T2", "gibibyte", "network_egress"),
        ("USE1-USW2-AWS-Out-Bytes", "gibibyte", "network_egress"),
    ],
)
def test_transferencia_entre_regioes_da_aws_e_rede(uso, unidade, esperado) -> None:
    assert _matcher("aws", AWS_RULES).match(uso, usage_unit=unidade).component_type == esperado


def test_volume_com_familia_aparente_nao_vira_memoria() -> None:
    """Sem regra nenhuma, só a L3: GB é volume; GiB-hora é memória."""
    l3 = SkuMatcher([], [])
    assert l3.match("USE1-USW2-Foo-Bytes", usage_unit="gibibyte").component_type != "ram"
    assert l3.match("N2 Instance Ram running in Americas").component_type == "ram"
    assert l3.match("N2D Custom Instance", usage_unit="gibibyte hour").component_type == "ram"


def test_ram_do_gcp_continua_ram() -> None:
    m = _matcher("gcp", GCP_RULES)
    assert (
        m.match("E2 Instance Ram running in Americas", usage_unit="gibibyte hour").component_type
        == "ram"
    )
    assert (
        m.match("N2 Instance Core running in Americas", usage_unit="hour").component_type == "core"
    )


# ── C2: regras da OCI com o nome da lista de preços oficial ─────────────────


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        ("Storage - Block Volume - Storage", "storage_ssd"),
        ("Storage - Block Volume - Performance Units", "storage_ssd"),
        ("Block Volume - Storage", "storage_ssd"),
        ("Object Storage - Storage", "object_storage"),
        ("Archive Storage - Storage", "object_storage"),
        ("File Storage - Storage", "file_storage"),
        ("OCI - FastConnect 1 Gbps", "network_egress"),
        ("Load Balancer Base", "network_egress"),
        ("Oracle Base Database Service - Database Storage", "database"),
        ("Oracle Autonomous AI Transaction Processing - ECPU", "database"),
        ("MySQL Database - ECPU", "database"),
        ("Compute - Standard - E4 - OCPU", "core"),
        ("Oracle Cloud Infrastructure Cache with Redis - Low Memory (up to 10 GB)", "database"),
        ("OCI Search with OpenSearch HA", "database"),
        ("Compute - Standard - A1 - Memory", "ram"),
        ("Outbound Data Transfer - Originating in North America, Europe, and UK", "network_egress"),
    ],
)
def test_oci_pelo_nome_da_lista_oficial(descricao, esperado) -> None:
    assert _matcher("oci", OCI_RULES).match(descricao).component_type == esperado


# ── C3 e C4: reclassificação pelo nome do serviço ───────────────────────────


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "esperado"),
    [
        ("aws", "AmazonDynamoDB", "USE1-ReadCapacityUnit-Hrs", "database"),
        ("aws", "AmazonDynamoDB", "USE1-TimedStorage-ByteHrs", "database"),
        ("aws", "Amazon DynamoDB", "USE1-WriteRequestUnits", "database"),
        ("aws", "AmazonDynamoDB", "USE1-DataTransfer-Out-Bytes", "network_egress"),
        ("aws", "AmazonES", "USE1-ESInstance:r6g.large", "database"),
        ("aws", "Amazon OpenSearch Service", "USE1-ES:GP3-Storage", "database"),
        ("aws", "AmazonEC2", "USE1-BoxUsage:m5.large", "core"),
        ("gcp", "AlloyDB", "AlloyDB Instance vCPU Cores in Iowa", "database"),
        ("gcp", "Cloud Bigtable", "Server Node", "database"),
        ("gcp", "Cloud Memorystore for Redis", "Redis Capacity Basic M1 Iowa", "database"),
        ("gcp", "Cloud Firestore", "Cloud Firestore Network Egress", "network_egress"),
        ("gcp", "Kubernetes Engine", "Autopilot Pod mCPU Requests (us-central1)", "serverless"),
        ("gcp", "Kubernetes Engine", "Zonal Kubernetes Clusters", "gke_control_plane"),
        (
            "azure",
            "Container Instances",
            "Container Instances | Standard vCPU Duration",
            "serverless",
        ),
    ],
)
def test_reclassificacao_pelo_servico(provedor, servico, uso, esperado) -> None:
    antes = (
        "network_egress"
        if "egress" in uso.lower() or "datatransfer" in uso.lower()
        else "unclassified"
    )
    if uso.startswith("USE1-BoxUsage"):
        antes = "core"
    if uso == "Zonal Kubernetes Clusters":
        antes = "gke_control_plane"
    assert reclassificar(provedor, servico, uso, antes) == esperado


CASOS_DE_CONTEINER = [
    # Container Instances: vCPU por hora, memória por GB-hora (Retail Prices API)
    (
        (
            "azure",
            "Container Instances",
            "Container Instances | Standard vCPU Duration",
            "720",
            "Hours",
        ),
        SERVERLESS_VCPU,
        "720",
    ),
    (
        (
            "azure",
            "Container Instances",
            "Container Instances | Standard Memory Duration",
            "1440",
            "GB Hour",
        ),
        SERVERLESS_MEMORIA,
        "1440",
    ),
    # Autopilot: mCPU-hora vira vCPU-hora; a memória vem em GiB-hora
    (
        ("gcp", "Kubernetes Engine", "Autopilot Pod mCPU Requests (us-central1)", "500000", "hour"),
        SERVERLESS_VCPU,
        "500",
    ),
    (
        (
            "gcp",
            "Kubernetes Engine",
            "Autopilot Scale-Out x86 Pod Memory Requests (us-central1)",
            "2000",
            "gibibyte hour",
        ),
        SERVERLESS_MEMORIA,
        "2000",
    ),
    # GPU e TPU do Autopilot ficam ao custo atual
    (
        ("gcp", "Kubernetes Engine", "Autopilot TPU v5e Premium (us-central1)", "10", "hour"),
        SERVERLESS,
        None,
    ),
]


@pytest.mark.parametrize(("linha", "categoria", "medida"), CASOS_DE_CONTEINER)
def test_conteiner_por_duracao(linha, categoria, medida) -> None:
    provedor, servico, uso, quantidade, unidade = linha
    tipo = tipo_por_uso("serverless", provedor, servico, uso)
    assert tipo.categoria == categoria
    valor = quantidade_por_uso(provedor, tipo, Decimal(quantidade), unidade)
    assert valor == (Decimal(medida) if medida else None)


# ── C5: reconhecido, sem cotação ────────────────────────────────────────────


def _linha(servico: str, uso: str, custo: str, componente: str = "unclassified") -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider="aws",
        billing_period="2026-09",
        service_name=servico,
        cost=Decimal(custo),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type=uso,
        component_type=componente,
    )


@pytest.mark.asyncio
async def test_servico_reconhecido_sem_regra_nao_e_nao_classificado() -> None:
    linhas = [
        _linha("AmazonRedshift", "USE1-Node:ra3.xlplus", "400"),
        _linha("AmazonBedrock", "USE1-Claude3Sonnet-input-tokens", "60"),
        _linha("ServicoQueNinguemConhece", "USE1-Coisa", "7"),
    ]
    aux = (
        await BillingAggregator().aggregate(linhas, "aws", {"AmazonRedshift", "AmazonBedrock"})
    ).auxiliary
    assert aux.por_uso == {RECONHECIDO_SEM_COTACAO: Decimal("460")}
    assert aux.por_servico[RECONHECIDO_SEM_COTACAO] == {
        "AmazonRedshift": Decimal("400"),
        "AmazonBedrock": Decimal("60"),
    }
    # Só o que ninguém reconhece fica em "não classificado"
    assert aux.orphan == Decimal("7")
    assert aux.orphan_skus == ("USE1-Coisa",)
    # Ao custo atual: sem quantidade, sem cotação
    assert RECONHECIDO_SEM_COTACAO not in aux.quantidade_por_regiao


@pytest.mark.asyncio
async def test_sem_a_lista_o_comportamento_e_o_de_antes() -> None:
    aux = (
        await BillingAggregator().aggregate([_linha("AmazonRedshift", "X", "5")], "aws")
    ).auxiliary
    assert aux.orphan == Decimal("5")
    assert not aux.por_uso


# ── C6: o nome da fatura e o do DE-PARA, com ou sem a marca ─────────────────


@pytest.mark.parametrize(
    ("provedor", "chave", "esperada"),
    [
        ("azure", "eventgrid", "azureeventgrid"),
        ("azure", "applicationinsights", "azureapplicationinsights"),
        ("gcp", "clouddataflow", "dataflow"),
        ("gcp", "cloudspanner", "spanner"),
        ("aws", "amazonbedrock", "awsbedrock"),
        ("aws", "comprehend", "amazoncomprehend"),
        ("oci", "queue", "ociqueue"),
    ],
)
def test_variantes_de_marca_acham_o_nome_do_de_para(provedor, chave, esperada) -> None:
    assert esperada in variantes_de_marca(provedor, chave)


def test_variante_nao_reduz_o_nome_a_quase_nada() -> None:
    # "AmazonQ" sem a marca seria "q": não vale
    assert "q" not in variantes_de_marca("aws", "amazonq")


@pytest.mark.parametrize(
    ("provedor", "servico", "uso"),
    [
        ("aws", "AWSSupportBusiness", "Dollar"),
        ("aws", "AWS Support (Enterprise)", "Dollar"),
        ("aws", "AWSDeveloperSupport", "Dollar"),
        ("gcp", "Support", "Enhanced Support"),
    ],
)
def test_plano_de_suporte_tem_balde_proprio(provedor, servico, uso) -> None:
    assert reclassificar(provedor, servico, uso, "unclassified") == "support"
