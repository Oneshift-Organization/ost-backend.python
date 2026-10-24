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
from app.domain.services.azure_shape import (
    familia_app_service,
    parse_azure_shape,
    plano_app_service,
)
from app.domain.services.categoria_por_uso import (
    API_OUTROS,
    API_REQUISICOES,
    ARQUIVOS,
    ARQUIVOS_CAPACIDADE,
    ENDPOINT_DADOS,
    ENDPOINT_HORAS,
    ENDPOINT_OUTROS,
    FUNCAO_COMPUTE,
    FUNCAO_CPU,
    FUNCAO_REQUISICOES,
    FUNCOES_OUTROS,
    LINK_1G,
    LINK_10G,
    LINK_100G,
    LINK_DADOS,
    LINK_GATEWAY,
    LINK_OUTROS,
    OBJETO_ESCRITA,
    OBJETO_LEITURA,
    OBJETO_OUTROS,
    RECONHECIDO_SEM_COTACAO,
    SERVERLESS,
    SERVERLESS_MEMORIA,
    SERVERLESS_VCPU,
    UNIDADE,
    quantidade_por_uso,
    tipo_por_uso,
)
from app.domain.services.reclassificacao_por_uso import reclassificar
from app.domain.services.service_identity import variantes_de_marca
from app.domain.services.sku_matcher import SkuMatcher
from app.infrastructure.database.seed_category_rates import category_rate_points
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


# ── Lote 2 (§7): App Service, API Gateway, requisição de objeto e arquivos ─


@pytest.mark.parametrize(
    ("medidor", "tipo", "familia", "vcpus", "ram"),
    [
        (
            "Azure App Service | Premium v3 Plan | P1 v3 App",
            "AppService_P1v3",
            "APPSERVICE_PV3",
            2,
            "8",
        ),
        (
            "Azure App Service | Premium v3 Plan | P0v3 App",
            "AppService_P0v3",
            "APPSERVICE_PV3",
            1,
            "4",
        ),
        (
            "Azure App Service | Premium v3 Plan | P1mv3 App",
            "AppService_P1mv3",
            "APPSERVICE_PMV3",
            2,
            "16",
        ),
        ("Azure App Service | Basic Plan | B1 App", "AppService_B1", "APPSERVICE_B", 1, "1.75"),
        (
            "Azure App Service | Isolated v2 Plan | I1 v2 App",
            "AppService_I1v2",
            "APPSERVICE_IV2",
            2,
            "8",
        ),
    ],
)
def test_plano_do_app_service_tem_tamanho(medidor, tipo, familia, vcpus, ram) -> None:
    assert plano_app_service(medidor) == tipo
    assert familia_app_service(tipo) == familia
    forma = parse_azure_shape(tipo)
    assert (forma.vcpus, forma.ram_gib) == (Decimal(vcpus), Decimal(ram))


@pytest.mark.parametrize(
    "medidor",
    [
        "Azure App Service | Premium Plan | P4 App",  # sem tamanho publicado
        "Azure App Service | Isolated Plan | Front End App",
        "Azure App Service | IP SSL | IP SSL Connection",
        "Virtual Machines | BS Series | B1s",
    ],
)
def test_o_que_nao_e_plano_com_tamanho_fica_de_fora(medidor) -> None:
    assert plano_app_service(medidor) is None


def test_plano_do_app_service_e_compute_pela_reclassificacao() -> None:
    servico = "Azure App Service"
    assert (
        reclassificar("azure", servico, f"{servico} | Premium v3 Plan | P1 v3 App", "unclassified")
        == "core"
    )
    assert (
        reclassificar("azure", servico, f"{servico} | Basic Plan | B2 App", "unclassified")
        == "core"
    )
    # O que não é plano com tamanho continua como estava
    assert (
        reclassificar("azure", servico, f"{servico} | IP SSL | IP SSL Connection", "unclassified")
        == "unclassified"
    )
    assert (
        reclassificar("azure", servico, f"{servico} | Premium Plan | P4 App", "unclassified")
        == "unclassified"
    )


@pytest.mark.asyncio
async def test_app_service_vira_pool_estimado() -> None:
    linha = BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider="azure",
        billing_period="2026-09",
        service_name="Azure App Service",
        cost=Decimal("220"),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type="Azure App Service | Premium v3 Plan | P1 v3 App",
        usage_amount=Decimal("1460"),
        usage_unit="hours",
        region="eastus",
        component_type="unclassified",
        attributes={
            "instance_type": "AppService_P1v3",
            "instance_family": "APPSERVICE_PV3",
            "capacity_unit": "instance",
            "charge_kind": "usage",
        },
    )
    resultado = await BillingAggregator().aggregate([linha], "azure")
    (pool,) = resultado.pools
    assert pool.pool_key.machine_family == "APPSERVICE_PV3"
    # 1.460 instâncias-hora x (2 vCPU, 8 GiB), estimado pelo tamanho publicado, na média
    # da janela de horas do mês
    assert pool.capacity.vcpus == Decimal("2920") / resultado.hours_window
    assert pool.capacity.ram_gib == Decimal("11680") / resultado.hours_window
    assert resultado.auxiliary.orphan == Decimal("0")


CASOS_DO_LOTE_2 = [
    # API Gateway: chamada da AWS contada uma a uma; a Azure em 10 mil; a OCI já chega do
    # parser em chamadas, com o número da unidade separado (§8)
    (
        ("api_gateway", "aws", "AmazonApiGateway", "USE1-ApiGatewayRequest", "20000", "Requests"),
        API_REQUISICOES,
        "2",
    ),
    (
        ("api_gateway", "aws", "AmazonApiGateway", "USE1-ApiGatewayHttpRequest", "10000", None),
        API_REQUISICOES,
        "1",
    ),
    (
        (
            "api_gateway",
            "azure",
            "API Management",
            "API Management | Consumption Calls",
            "300",
            "10K",
        ),
        API_REQUISICOES,
        "300",
    ),
    (
        (
            "api_gateway",
            "oci",
            "API_GATEWAY",
            "API Gateway  - 1,000,000 API Calls",
            "3000000",
            "API Calls Per Month",
        ),
        API_REQUISICOES,
        "300",
    ),
    (
        (
            "api_gateway",
            "azure",
            "API Management",
            "API Management | Developer Unit",
            "730",
            "1 Hour",
        ),
        API_OUTROS,
        None,
    ),
    # Requisição de objeto: escrita e leitura
    (
        ("object_requests", "aws", "AmazonS3", "USE1-Requests-Tier1", "50000", "Requests"),
        OBJETO_ESCRITA,
        "5",
    ),
    (
        ("object_requests", "aws", "AmazonS3", "Requests-Tier2", "100000", None),
        OBJETO_LEITURA,
        "10",
    ),
    (
        ("object_requests", "aws", "AmazonS3", "SAE1-Requests-INT-Tier1", "10000", None),
        OBJETO_ESCRITA,
        "1",
    ),
    (
        (
            "object_requests",
            "azure",
            "Storage",
            "Storage | Blob Storage | Hot LRS Write Operations",
            "7",
            "10K",
        ),
        OBJETO_ESCRITA,
        "7",
    ),
    (
        (
            "object_requests",
            "azure",
            "Storage",
            "Storage | Blob Storage | Hot Read Operations",
            "9",
            "10K",
        ),
        OBJETO_LEITURA,
        "9",
    ),
    (
        (
            "object_requests",
            "gcp",
            "Cloud Storage",
            "Regional Standard Class A Operations",
            "30000",
            "count",
        ),
        OBJETO_ESCRITA,
        "3",
    ),
    (
        ("object_requests", "oci", "OBJECTSTORE", "Object Storage - Requests", "5", "x"),
        OBJETO_OUTROS,
        None,
    ),
    # Arquivos por GB-mês; IA e Archive do EFS ficam ao custo atual
    (
        ("file_storage", "aws", "AmazonEFS", "USE1-TimedStorage-ByteHrs", "100", "GB-Mo"),
        ARQUIVOS_CAPACIDADE,
        "100",
    ),
    (
        ("file_storage", "aws", "AmazonEFS", "USE1-IATimedStorage-ByteHrs", "100", "GB-Mo"),
        ARQUIVOS,
        None,
    ),
    (
        (
            "file_storage",
            "azure",
            "Storage",
            "Storage | Premium Files | Premium LRS Provisioned",
            "1024",
            "1 GB/Month",
        ),
        ARQUIVOS_CAPACIDADE,
        "1024",
    ),
    (
        (
            "file_storage",
            "gcp",
            "Cloud Filestore",
            "Filestore Capacity Basic HDD (Standard) Iowa",
            "1024",
            "gibibyte month",
        ),
        ARQUIVOS_CAPACIDADE,
        "1024",
    ),
    (
        ("file_storage", "oci", "FILE_STORAGE", "File Storage - Storage", "500", "x"),
        ARQUIVOS_CAPACIDADE,
        "500",
    ),
]


@pytest.mark.parametrize(("linha", "categoria", "medida"), CASOS_DO_LOTE_2)
def test_lote_2_categoria_e_quantidade(linha, categoria, medida) -> None:
    balde, provedor, servico, uso, quantidade, unidade = linha
    tipo = tipo_por_uso(balde, provedor, servico, uso)
    assert tipo.categoria == categoria
    valor = quantidade_por_uso(provedor, tipo, Decimal(quantidade), unidade)
    assert valor == (Decimal(medida) if medida else None)


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "esperado"),
    [
        ("aws", "AmazonApiGateway", "USE1-ApiGatewayRequest", "api_gateway"),
        ("aws", "AmazonApiGateway", "USE1-DataTransfer-Out-Bytes", "network_egress"),
        ("aws", "AmazonS3", "USE1-Requests-Tier2", "object_requests"),
        ("aws", "AmazonS3", "USE1-TimedStorage-ByteHrs", "object_storage"),
        ("azure", "API Management", "API Management | Consumption Calls", "api_gateway"),
        (
            "azure",
            "Storage",
            "Storage | Blob Storage | Hot LRS Write Operations",
            "object_requests",
        ),
        ("azure", "Storage", "Storage | Files v2 | Hot LRS Write Operations", "file_storage"),
        ("gcp", "Cloud Storage", "Regional Standard Class B Operations", "object_requests"),
        ("oci", "API_GATEWAY", "API Gateway  - 1,000,000 API Calls", "api_gateway"),
        ("oci", "OBJECTSTORE", "Object Storage - Requests", "object_requests"),
    ],
)
def test_lote_2_reclassificacao(provedor, servico, uso, esperado) -> None:
    antes = {
        "USE1-DataTransfer-Out-Bytes": "network_egress",
        "USE1-TimedStorage-ByteHrs": "object_storage",
    }
    assert reclassificar(provedor, servico, uso, antes.get(uso, "unclassified")) == esperado


def test_taxas_do_lote_2() -> None:
    taxas = {(p.provider, p.service_type): p for p in category_rate_points()}
    for categoria in (API_REQUISICOES, OBJETO_ESCRITA, OBJETO_LEITURA, ARQUIVOS_CAPACIDADE):
        for prov in ("aws", "azure", "gcp", "oci"):
            assert taxas[(prov, categoria)].unit == UNIDADE[categoria], (prov, categoria)


# ── Lote 3 (§8): Functions, link dedicado e endpoint privado ─────────────

_GIB_HORA_DE = Decimal(1) / Decimal(3600)

CASOS_DO_LOTE_3 = [
    # Functions: memória vezes tempo em GiB-hora; a chamada em 10 mil
    (
        ("functions", "aws", "AWSLambda", "USE1-Lambda-GB-Second", "3600000", "Lambda-GB-Second"),
        FUNCAO_COMPUTE,
        "1000",
    ),
    (
        ("functions", "aws", "AWSLambda", "EUC1-Lambda-GB-Second-ARM", "36000", None),
        FUNCAO_COMPUTE,
        "10",
    ),
    (
        ("functions", "aws", "AWSLambda", "USE1-Request", "40000000", "Requests"),
        FUNCAO_REQUISICOES,
        "4000",
    ),
    (("functions", "aws", "AWSLambda", "Request-ARM", "10000", None), FUNCAO_REQUISICOES, "1"),
    # Concorrência provisionada e Edge ficam ao custo atual
    (
        ("functions", "aws", "AWSLambda", "USE1-Lambda-Provisioned-GB-Second", "1", None),
        FUNCOES_OUTROS,
        None,
    ),
    (
        ("functions", "aws", "AWSLambda", "USE1-Lambda-Edge-Request", "1", None),
        FUNCOES_OUTROS,
        None,
    ),
    # Azure: a quantidade vem na unidade da Retail Prices API (GB-segundo; execução em 10)
    (
        (
            "functions",
            "azure",
            "Functions",
            "Functions | Functions | Standard Execution Time",
            "1800000",
            "x",
        ),
        FUNCAO_COMPUTE,
        "500",
    ),
    (
        (
            "functions",
            "azure",
            "Functions",
            "Functions | Flex Consumption | On Demand Total Executions",
            "50000",
            "x",
        ),
        FUNCAO_REQUISICOES,
        "50",
    ),
    (
        (
            "functions",
            "azure",
            "Functions",
            "Functions | Premium Functions | Premium vCPU Duration",
            "730",
            "1 Hour",
        ),
        FUNCOES_OUTROS,
        None,
    ),
    # GCP: CPU à parte (a zero no destino); instância mínima fica
    (
        (
            "functions",
            "gcp",
            "Cloud Run Functions",
            "Cloud Run functions Memory (Request-based billing) in us-central1",
            "7200",
            "gibibyte second",
        ),
        FUNCAO_COMPUTE,
        "2",
    ),
    (
        (
            "functions",
            "gcp",
            "Cloud Run Functions",
            "Cloud Run functions CPU (Request-based billing) in us-central1",
            "3600",
            "second",
        ),
        FUNCAO_CPU,
        "1",
    ),
    (
        (
            "functions",
            "gcp",
            "Cloud Run Functions",
            "Cloud Run Functions Invocations",
            "20000",
            "count",
        ),
        FUNCAO_REQUISICOES,
        "2",
    ),
    (
        (
            "functions",
            "gcp",
            "Cloud Run Functions",
            "Cloud Run functions Min-Instance Memory (Request-based billing) in us-central1",
            "3600",
            "gibibyte second",
        ),
        FUNCOES_OUTROS,
        None,
    ),
    # Link dedicado: porta por velocidade e saída pelo link
    (
        ("dedicated_link", "aws", "AWSDirectConnect", "USE1-165HS-PortUsage:1G", "744", None),
        LINK_1G,
        "744",
    ),
    (
        ("dedicated_link", "aws", "AWSDirectConnect", "USE1-165HS-HCPortUsage:10G", "744", None),
        LINK_10G,
        "744",
    ),
    (
        ("dedicated_link", "aws", "AWSDirectConnect", "USE1-165HS-PortUsage:100G", "1", None),
        LINK_100G,
        "1",
    ),
    (
        ("dedicated_link", "aws", "AWSDirectConnect", "USE1-165HS-DataXfer-Out:dc.3", "500", None),
        LINK_DADOS,
        "500",
    ),
    # Tarifa fixa, hospedada de 500 Mbps e o `:dxfr` (US$ 0) ficam
    (
        (
            "dedicated_link",
            "aws",
            "AWSDirectConnect",
            "USE1-165HS-FlatRatePortUsage:10G-Tier1",
            "1",
            None,
        ),
        LINK_OUTROS,
        None,
    ),
    (
        ("dedicated_link", "aws", "AWSDirectConnect", "USE1-165HS-HCPortUsage:500M", "1", None),
        LINK_OUTROS,
        None,
    ),
    (
        ("dedicated_link", "aws", "AWSDirectConnect", "USE1-AEBTF-DataXfer-Out:dxfr", "1", None),
        LINK_OUTROS,
        None,
    ),
    # Azure: o circuito vem em mês
    (
        (
            "dedicated_link",
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute | Standard Metered Data 1 Gbps Circuit",
            "1",
            "1/Month",
        ),
        LINK_1G,
        "730",
    ),
    (
        (
            "dedicated_link",
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute Direct | Port 100 Gbps Port",
            "0.5",
            "1/Month",
        ),
        LINK_100G,
        "365.0",
    ),
    (
        (
            "dedicated_link",
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute | Metered Data - Data Transfer Out",
            "200",
            "1 GB",
        ),
        LINK_DADOS,
        "200",
    ),
    (
        (
            "dedicated_link",
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute Gateway | ErGw1AZ Gateway",
            "730",
            "1 Hour",
        ),
        LINK_GATEWAY,
        "730",
    ),
    (
        (
            "dedicated_link",
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute | Premium Metered Data 10 Gbps Circuit",
            "1",
            "1/Month",
        ),
        LINK_OUTROS,
        None,
    ),
    (
        (
            "dedicated_link",
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute | Standard Unlimited Data 10 Gbps Circuit",
            "1",
            "1/Month",
        ),
        LINK_OUTROS,
        None,
    ),
    # GCP: anexo do Partner, circuito dedicado, anexo do Dedicated (a zero) e saída
    (
        (
            "dedicated_link",
            "gcp",
            "Networking",
            "Cloud Interconnect - 1Gbps VLAN attachment via Google partner",
            "744",
            "hour",
        ),
        LINK_1G,
        "744",
    ),
    (
        (
            "dedicated_link",
            "gcp",
            "Networking",
            "Cloud Interconnect - 10Gbps Dedicated circuit",
            "744",
            "hour",
        ),
        LINK_10G,
        "744",
    ),
    (
        (
            "dedicated_link",
            "gcp",
            "Networking",
            "Cloud Interconnect - 10Gbps VLAN attachment via Dedicated Interconnect",
            "744",
            "hour",
        ),
        LINK_GATEWAY,
        "744",
    ),
    (
        (
            "dedicated_link",
            "gcp",
            "Networking",
            "Cloud Interconnect - Local Data Transfer in UnitedStates",
            "300",
            "gibibyte",
        ),
        LINK_DADOS,
        "300",
    ),
    (
        (
            "dedicated_link",
            "gcp",
            "Networking",
            "Cloud Interconnect for MPS - 10Gbps Dedicated circuit",
            "744",
            "hour",
        ),
        LINK_OUTROS,
        None,
    ),
    (
        ("dedicated_link", "oci", "NETWORK", "OCI - FastConnect 10 Gbps", "744", "hours"),
        LINK_10G,
        "744",
    ),
    (
        ("dedicated_link", "oci", "NETWORK", "OCI - FastConnect 400 Gbps", "744", "hours"),
        LINK_OUTROS,
        None,
    ),
    # Endpoint privado por hora e GB
    (
        ("private_endpoint", "aws", "AmazonVPC", "USE1-VpcEndpoint-Hours", "2200", None),
        ENDPOINT_HORAS,
        "2200",
    ),
    (
        ("private_endpoint", "aws", "AmazonVPC", "USE1-VpcEndpoint-Bytes", "600", None),
        ENDPOINT_DADOS,
        "600",
    ),
    (
        (
            "private_endpoint",
            "azure",
            "Virtual Network",
            "Virtual Network | Virtual Network Private Link | Standard Private Endpoint",
            "730",
            "1 Hour",
        ),
        ENDPOINT_HORAS,
        "730",
    ),
    (
        (
            "private_endpoint",
            "azure",
            "Virtual Network",
            "Virtual Network | Virtual Network Private Link | Standard Data Processed - Egress",
            "50",
            "1 GB",
        ),
        ENDPOINT_DADOS,
        "50",
    ),
    (
        (
            "private_endpoint",
            "gcp",
            "Networking",
            "Networking Private Service Connect Consumer End Point",
            "744",
            "hour",
        ),
        ENDPOINT_HORAS,
        "744",
    ),
    (
        (
            "private_endpoint",
            "gcp",
            "Networking",
            "Networking Private Service Connect Consumer Data Processing",
            "80",
            "gibibyte",
        ),
        ENDPOINT_DADOS,
        "80",
    ),
    (
        (
            "private_endpoint",
            "gcp",
            "Networking",
            "Network Private Service Connect Google Managed Service GKE"
            " Consumer Data Processing Primary",
            "80",
            "gibibyte",
        ),
        ENDPOINT_OUTROS,
        None,
    ),
]


@pytest.mark.parametrize(("linha", "categoria", "medida"), CASOS_DO_LOTE_3)
def test_lote_3_categoria_e_quantidade(linha, categoria, medida) -> None:
    balde, provedor, servico, uso, quantidade, unidade = linha
    tipo = tipo_por_uso(balde, provedor, servico, uso)
    assert tipo.categoria == categoria
    valor = quantidade_por_uso(provedor, tipo, Decimal(quantidade), unidade)
    assert valor == (Decimal(medida) if medida else None)


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "esperado"),
    [
        ("aws", "AWSLambda", "USE1-Lambda-GB-Second", "functions"),
        ("aws", "AWSDirectConnect", "USE1-165HS-DataXfer-Out", "dedicated_link"),
        ("aws", "AmazonVPC", "USE1-VpcEndpoint-Bytes", "private_endpoint"),
        # Vizinhos: VPN e Transit Gateway seguem na rede; a requisição do SQS não é do S3
        ("aws", "AmazonVPC", "USE1-VPN-Usage-Hours:ipsec.1", "network_egress"),
        ("aws", "AmazonVPC", "USE1-TransitGateway-Hours", "network_egress"),
        ("aws", "AWSQueueService", "USE1-Requests-Tier1", "unclassified"),
        ("azure", "Functions", "Functions | Functions | Standard Execution Time", "functions"),
        (
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute | Standard Metered Data 1 Gbps Circuit",
            "dedicated_link",
        ),
        (
            "azure",
            "Azure Container Apps",
            "Azure Container Apps | Standard | Standard Requests",
            "serverless",
        ),
        ("gcp", "Cloud Run Functions", "Cloud Run Functions Invocations", "functions"),
        ("gcp", "Cloud Run", "Services CPU (Request-based billing)", "serverless"),
        (
            "gcp",
            "Compute Engine",
            "Cloud Interconnect - 10Gbps Dedicated circuit",
            "dedicated_link",
        ),
        ("oci", "FUNCTIONS", "Oracle Functions - Invocations", "functions"),
        ("oci", "NETWORK", "OCI - FastConnect 1 Gbps", "dedicated_link"),
    ],
)
def test_lote_3_reclassificacao(provedor, servico, uso, esperado) -> None:
    antes = {
        "USE1-VPN-Usage-Hours:ipsec.1": "network_egress",
        "USE1-TransitGateway-Hours": "network_egress",
    }
    assert reclassificar(provedor, servico, uso, antes.get(uso, "unclassified")) == esperado


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "unidade"),
    [
        # A escrita da classe fria custa de 2 a 7 vezes a da padrão: fica ao custo atual (§8)
        ("aws", "AmazonS3", "USE1-Requests-SIA-Tier1", None),
        ("aws", "AmazonS3", "USE1-Requests-GLACIER-Tier1", None),
        ("aws", "AmazonS3", "USE1-Requests-BACKUP-Tier1", None),
        ("azure", "Storage", "Storage | Blob Storage | Archive GRS Write Operations", "10K"),
        ("azure", "Storage", "Storage | General Block Blob v2 | Cool LRS Read Operations", "10K"),
        (
            "gcp",
            "Cloud Storage",
            "Multi-Region Durable Reduced Availability Class A Operations",
            "count",
        ),
        ("gcp", "Cloud Storage", "Coldline Storage Class B Operations", "count"),
    ],
)
def test_requisicao_da_classe_fria_fica_ao_custo_atual(provedor, servico, uso, unidade) -> None:
    tipo = tipo_por_uso("object_requests", provedor, servico, uso)
    assert tipo.categoria == OBJETO_OUTROS
    assert quantidade_por_uso(provedor, tipo, Decimal(1000), unidade) is None


def test_taxas_do_lote_3() -> None:
    taxas = {(p.provider, p.service_type): p for p in category_rate_points()}
    cotadas = (
        FUNCAO_COMPUTE,
        FUNCAO_CPU,
        FUNCAO_REQUISICOES,
        LINK_1G,
        LINK_10G,
        LINK_100G,
        LINK_DADOS,
        LINK_GATEWAY,
        ENDPOINT_HORAS,
        ENDPOINT_DADOS,
    )
    for categoria in cotadas:
        for prov in ("aws", "azure", "gcp", "oci"):
            assert taxas[(prov, categoria)].unit == UNIDADE[categoria], (prov, categoria)
    # O que o GB-segundo do destino ou a porta já cobrem entra a zero em todos
    for categoria in (FUNCAO_CPU, LINK_GATEWAY):
        assert {taxas[(p, categoria)].price for p in ("aws", "azure", "gcp", "oci")} == {Decimal(0)}
    # A taxa do GB-segundo é o preço publicado por GiB-segundo vezes 3.600
    assert taxas[("aws", FUNCAO_COMPUTE)].price == Decimal("0.06")
