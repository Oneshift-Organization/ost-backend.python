from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.domain.analysis import (
    BillingItemSummary,
    EquivalentService,
    FinancialEstimate,
    MigrationContext,
    ServiceCategoryGroup,
    ServiceComparison,
    SourceUsage,
    TechnicalMatch,
    WorkloadSummary,
)
from app.domain.entities.workload_pool import AuxiliaryCosts, FamilyPool, WorkloadPool
from app.domain.interfaces.i_service_repositories import (
    IServiceComparisonRepository,
    IServiceMetadataRepository,
)
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.repositories.i_exchange_rate_repo import IExchangeRateRepository
from app.domain.services.alertas import avaliar_cambio, avaliar_regioes
from app.domain.services.banco_por_uso import (
    ARMAZENAMENTO,
    BACKUP,
    CACHE,
    CATEGORIA_BACKUP,
    CATEGORIA_BACKUP_DO_MOTOR,
    CATEGORIA_DA_FAIXA,
    CATEGORIA_DO_MOTOR,
    CATEGORIA_STORAGE,
    CATEGORIA_STORAGE_DO_MOTOR,
    OUTROS,
    SEM_EQUIVALENTE,
    copias_do_storage,
    fator_ha,
    ler_chave_da_parte,
    ler_chave_do_motor,
    pesos_no_motor,
)
from app.domain.services.banco_por_uso import CATEGORIA as CATEGORIA_BANCO
from app.domain.services.banco_por_uso import UNIDADE as UNIDADE_DO_BANCO
from app.domain.services.categoria_por_uso import UNIDADE as UNIDADE_POR_USO
from app.domain.services.categoria_por_zona import dividir_por_zona
from app.domain.services.category_calculator import (
    CategoryEquivalent,
    CategoryRate,
    estimate_by_cost_ratio,
    estimate_by_quantity,
)
from app.domain.services.cobertura_por_compromisso import (
    LIMIAR_DE_COBERTURA,
    avaliar_cobertura,
    cobertura_estimada,
    familia_do_ponto,
)
from app.domain.services.confidence import avaliar_confianca
from app.domain.services.currency import (
    ANALYSIS_CURRENCY,
    FxRate,
    as_ledger,
    convert,
    identity_rate,
    taxa_desconhecida,
)
from app.domain.services.equivalence_matcher import EquivalenceMatcher
from app.domain.services.migration_expectation import (
    derive_expectation,
    learn_class_defaults,
)
from app.domain.services.perfil_de_familia import perfil
from app.domain.services.pool_aggregation import aggregate_by_family
from app.domain.services.premissas_detalhadas import (
    base_de_custo,
    desconto_origem_sobre_lista,
    fracao,
)
from app.domain.services.provider_consolidation import (
    ProviderTally,
    consolidate,
    weakest_confidence,
)
from app.domain.services.reconciliation import reconcile
from app.domain.services.rede_por_uso import EGRESS as CATEGORIA_EGRESS
from app.domain.services.rede_por_uso import FRANQUIA_DE_EGRESS_GB
from app.domain.services.rede_por_uso import UNIDADE as UNIDADE_DA_REDE
from app.domain.services.regioes import (
    ZONA_PADRAO,
    escolher_regiao_de_preco,
    regiao_canonica,
    regiao_da_linha,
    regiao_dominante,
    zona_da_regiao,
)
from app.domain.services.service_identity import (
    financial_category,
    is_financial_component,
    service_key,
    service_type_hint,
)
from app.domain.services.servico_pelo_uso import servico_pelo_uso
from app.services.billing_aggregator import BillingAggregator
from app.use_cases.analysis.leitura_completa import listar_todos


@dataclass(frozen=True)
class BuildMigrationContextInput:
    user_id: UUID
    report_id: UUID


def _reviewed_billing_identity(item, provider: str):
    """Correct known aliases in memory; do not reprice a registry as a disk."""
    if is_financial_component(item.service_name, item.category or ""):
        category = financial_category(item.service_name, item.category or "")
        component = "commitment" if category in {"commitment", "compromisso"} else "unclassified"
        return replace(item, component_type=component, category=category)
    hint = service_type_hint(provider, item.service_name)
    if hint and hint[1] != item.service_type:
        return replace(item, category=hint[0], service_type=hint[1], component_type="unclassified")
    return item


# Serviço com mais de uma linha no DE-PARA, quando o classificador não indica o tipo: sem
# isto vence a primeira em ordem alfabética (PLANO_EQUIVALENTES_E_TARIFAS §7 e §9). O
# Cosmos DB é banco de documentos, não SQL distribuído; o Data Factory é ETL, não rotulagem
# de dados; o VPN Gateway é VPN, não roteamento; o OCI Streaming é Kafka, não fila
TIPO_PREFERIDO: dict[str, dict[str, str]] = {
    "azure": {
        "Azure Cosmos DB": "document_nosql",
        "Azure Data Factory": "etl_data_integration",
        "Azure VPN Gateway": "vpn_gateway",
    },
    "oci": {"OCI Streaming": "stream_ingestion"},
}

PROVIDER_SERVICE_ALIASES: dict[str, dict[str, str]] = {
    "aws": {
        "EC2-Instances": "Amazon EC2",
        "EC2-Other": "Amazon EC2",
        "EC2 Container Registry (ECR)": "Amazon Elastic Container Registry (ECR)",
        "Elastic Container Service for Kubernetes": "Amazon Elastic Kubernetes Service (EKS)",
        "Elastic Container Service": "Amazon Elastic Container Service (ECS)",
        "Relational Database Service": "Amazon RDS",
        "Elastic File System": "Amazon Elastic File System (EFS)",
        "Key Management Service": "AWS Key Management Service (KMS)",
        "Lambda": "AWS Lambda",
        "Elastic Load Balancing": "Elastic Load Balancing",
        "DMS": "AWS Database Migration Service",
        "Q": "Amazon Q Business",
        "DocumentDB (with MongoDB compatibility)": "Amazon DocumentDB",
        "CloudWatch Events": "Amazon CloudWatch",
        "Elastic Container Registry Public": "Amazon Elastic Container Registry (ECR)",
        "Kinesis Firehose": "Amazon Kinesis Data Streams",
        "Simple Workflow Service": "AWS Step Functions",
        # Código do produto (`line_item_product_code` no CUR 2.0, `ProductCode` na fatura
        # detalhada) que não se parece com o nome comercial. Os que se parecem
        # (`AWSLambda`, `AmazonRoute53`) já casam pela normalização. Sem estes, o AWSELB
        # de uma conta real saía "tipo inferido" e sem equivalente em nenhum destino.
        "AWSELB": "Elastic Load Balancing",
        # MSK: Kafka gerenciado, no tipo de streaming do DE-PARA (lote v4, V4.3)
        "AmazonMSK": "Amazon MSK",
        "Amazon Managed Streaming for Apache Kafka": "Amazon MSK",
        "AWSQueueService": "Amazon Simple Queue Service (SQS)",
        "AWSKMS": "AWS Key Management Service (KMS)",
        "awskms": "AWS Key Management Service (KMS)",
        "AmazonStates": "AWS Step Functions",
        "ElasticMapReduce": "Amazon EMR",
        "AmazonES": "Amazon OpenSearch Service",
        "AmazonDocDB": "Amazon DocumentDB",
        "AmazonECR": "Amazon Elastic Container Registry (ECR)",
        "AmazonECRPublic": "Amazon Elastic Container Registry (ECR)",
        "AmazonECS": "Amazon Elastic Container Service (ECS)",
        "AmazonEKS": "Amazon Elastic Kubernetes Service (EKS)",
        "AmazonEFS": "Amazon Elastic File System (EFS)",
        "AmazonInspectorV2": "Amazon Inspector",
        # Nome do produto no CUR (`product_product_name`) diferente do nome do DE-PARA
        "Amazon Elastic Load Balancing": "Elastic Load Balancing",
        "Amazon Elastic Compute Cloud": "Amazon EC2",
        "Amazon Simple Storage Service": "Amazon S3",
        "Amazon Relational Database Service": "Amazon RDS",
        "Amazon Virtual Private Cloud": "Amazon VPC",
        "Amazon Simple Notification Service": "Amazon SNS",
        "Amazon Simple Email Service": "Amazon SES",
        "Amazon Simple Queue Service": "Amazon Simple Queue Service (SQS)",
        "AWS Key Management Service": "AWS Key Management Service (KMS)",
        "Amazon Elastic Container Service for Kubernetes": (
            "Amazon Elastic Kubernetes Service (EKS)"
        ),
        "Amazon Elastic Container Registry": "Amazon Elastic Container Registry (ECR)",
        "Amazon Elastic File System": "Amazon Elastic File System (EFS)",
    },
    # Categoria de medidor do Cost Details (`MeterCategory`). "Storage" fica de fora:
    # é disco ou blob, e o classificador decide pelo medidor.
    "azure": {
        "Virtual Machines": "Azure Virtual Machines",
        "Load Balancer": "Azure Load Balancer",
        "Azure Kubernetes Service": "Azure Kubernetes Service (AKS)",
        "SQL Database": "Azure SQL Database",
        "Functions": "Azure Functions",
        "Container Registry": "Azure Container Registry",
        "Key Vault": "Azure Key Vault",
        "Redis Cache": "Azure Cache for Redis",
        "Azure Front Door Service": "Azure Front Door",
        "Virtual Network": "Azure Virtual Network (VNet)",
        "Log Analytics": "Azure Monitor Logs",
        "Service Bus": "Azure Service Bus",
        "Event Hubs": "Azure Event Hubs",
        "Backup": "Azure Backup",
        "NAT Gateway": "Azure NAT Gateway",
        "VPN Gateway": "Azure VPN Gateway",
        "Content Delivery Network": "Azure CDN",
        "Application Gateway": "Azure Application Gateway",
    },
    # Descrição do serviço (Cost Table, BigQuery). "Networking" fica de fora: é
    # balanceador, NAT, IP ou VPN, e o classificador decide pelo SKU.
    "gcp": {
        "Kubernetes Engine": "Google Kubernetes Engine (GKE)",
        "Cloud Filestore": "Filestore",
        "Cloud Pub/Sub": "Pub/Sub",
        "App Engine": "Google App Engine",
        "Source Repository": "Cloud Source Repositories",
        "Cloud Memorystore for Redis": "Memorystore",
        "Cloud Functions": "Cloud Run functions",
    },
    # Código do serviço no relatório de custos. "DATABASE" e "NETWORK" ficam de fora:
    # cobrem mais de um serviço (Autonomous, Base DB, MySQL; VCN, egress, FastConnect).
    "oci": {
        "COMPUTE": "Oracle Cloud Infrastructure Compute",
        "OBJECT_STORAGE": "Oracle Cloud Infrastructure Object Storage",
        "BLOCK_STORAGE": "OCI Block Volumes",
        "LOAD_BALANCER": "OCI Load Balancing",
        "OKE": "Container Engine for Kubernetes (OKE)",
        "CONTAINER_ENGINE": "Container Engine for Kubernetes (OKE)",
        "FILE_STORAGE": "OCI File Storage",
        "FUNCTIONS": "Oracle Functions",
        "VAULT": "OCI Vault",
        "KMS": "OCI Vault",
        "REGISTRY": "Oracle Cloud Infrastructure Registry",
        "MONITORING": "OCI Monitoring",
        "LOGGING": "OCI Logging",
        "DNS": "Oracle DNS",
    },
}


_RANK_CONFIANCA = {"official": 2, "reference": 1, "low": 0}


def _somar_partes(
    cotadas: list[tuple[Any, dict[str, CategoryRate], list[Any], Decimal]],
) -> dict[str, tuple[Decimal, Decimal, str]]:
    """Provedor → (custo, delta, confiança) somando as zonas.

    Zona sem taxa para um provedor usa a razão que esse provedor teve na parte maior,
    para a soma não sair menor só porque falta a taxa regional. A confiança é a mais
    fraca entre a parte maior e as que pesam (20% ou mais do custo): uma fatia pequena
    em zona sem taxa oficial não rebaixa a categoria inteira."""
    _, _, principais, custo_principal = cotadas[0]
    total = sum((c for *_, c in cotadas), Decimal(0))
    razao = {
        e.provider: (e.estimated_monthly_cost / custo_principal) if custo_principal > 0 else None
        for e in principais
    }
    somas: dict[str, tuple[Decimal, Decimal, str]] = {}
    for indice, (_parte, _rates, equivs, custo) in enumerate(cotadas):
        da_parte = {e.provider: e for e in equivs}
        pesa = indice == 0 or (total > 0 and custo / total >= _PARTE_QUE_PESA)
        for prov, r in razao.items():
            if r is None:
                continue
            e = da_parte.get(prov)
            estimado = e.estimated_monthly_cost if e else custo * r
            confianca = e.pricing_confidence if e else "low"
            antes = somas.get(prov)
            if antes is None:
                somas[prov] = (estimado, estimado - custo, confianca)
                continue
            somas[prov] = (
                antes[0] + estimado,
                antes[1] + (estimado - custo),
                _mais_fraca(antes[2], confianca) if pesa else antes[2],
            )
    return somas


_PARTE_QUE_PESA = Decimal("0.2")


def _misturar_motores(
    base: dict[str, CategoryRate],
    por_motor: Mapping[str, dict[str, CategoryRate]],
    pesos: Mapping[str, Decimal],
) -> dict[str, CategoryRate]:
    """Taxa de banco de cada provedor pela vCPU-hora de cada motor na fatura: MySQL na
    taxa do MySQL, PostgreSQL na do PostgreSQL. Em alta disponibilidade (chave `:ha`), a
    vCPU-hora da origem conta 2 instâncias e vale no destino o fator de réplicas dele
    (OCI MySQL: 3). Sem motor, a maior taxa entre os motores, premissa declarada. Sem a
    taxa do motor no provedor, vale a base."""
    total = sum(pesos.values(), Decimal(0))
    if total <= 0:
        return base
    misturadas: dict[str, CategoryRate] = {}
    for prov, b in base.items():
        taxa = Decimal(0)
        for chave, peso in pesos.items():
            motor_, ha = ler_chave_do_motor(chave)
            taxa += (peso / total) * _taxa_do_motor(prov, b, por_motor, motor_, ha=ha)
        misturadas[prov] = replace(b, rate=taxa)
    return misturadas


def _taxa_do_motor(
    prov: str,
    base: CategoryRate,
    por_motor: Mapping[str, dict[str, CategoryRate]],
    motor_: str | None,
    *,
    ha: bool,
) -> Decimal:
    def efetiva(m: str | None) -> Decimal:
        taxa = por_motor.get(m or "", {}).get(prov)
        return (taxa or base).rate * (fator_ha(prov, m) if ha else Decimal(1))

    if motor_ is not None:
        return efetiva(motor_)
    return max([efetiva(None), *(efetiva(m) for m in CATEGORIA_DO_MOTOR if por_motor.get(m))])


def _resto_do_banco(
    partes: Mapping[str, Mapping[str, Decimal]],
    *,
    custos: Mapping[str, Decimal],
    storage: Mapping[str | None, dict[str, CategoryRate]],
    backup: Mapping[str | None, dict[str, CategoryRate]],
    cache: Mapping[str, dict[str, CategoryRate]],
    provedores: Sequence[str],
) -> tuple[dict[str, Decimal], dict[str, dict[str, Decimal]]]:
    """O banco fora das instâncias no destino (PLANO_PRECISAO_OCI B1 a B4): cache pelo
    GB-hora da faixa do nó; armazenamento pelos GB-mês x cópias da alta disponibilidade
    do destino; backup pelos GB-mês; o que só a origem cobra (IOPS provisionado,
    throughput, créditos, extended support, IO) a zero; outros ao custo da origem.
    Parte sem quantidade, ou sem taxa no destino, fica ao custo da origem.

    `custos`: custo de cada parte na moeda da análise. Devolve, por provedor, a razão
    destino ÷ origem do resto e o custo de cada tipo de parte no destino."""
    origem = sum(custos.values(), Decimal(0))
    fatores: dict[str, Decimal] = {}
    detalhes: dict[str, dict[str, Decimal]] = {}
    for prov in provedores:
        detalhe: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for chave, valores in partes.items():
            tipo, motor_, ha, faixa = ler_chave_da_parte(chave)
            custo = custos.get(chave, Decimal(0))
            qtd = valores.get("quantidade") or Decimal(0)
            taxa = _taxa_da_parte(
                prov,
                tipo,
                motor_=motor_,
                ha=ha,
                faixa=faixa,
                storage=storage,
                backup=backup,
                cache=cache,
            )
            if tipo == SEM_EQUIVALENTE:
                destino = Decimal(0)
            elif tipo == OUTROS or taxa is None or qtd <= 0:
                destino = custo
            else:
                destino = qtd * taxa
            detalhe[tipo] += destino
        detalhes[prov] = dict(detalhe)
        if origem > 0:
            fatores[prov] = sum(detalhe.values(), Decimal(0)) / origem
    return fatores, detalhes


def _taxa_da_parte(
    prov: str,
    tipo: str,
    *,
    motor_: str | None,
    ha: bool,
    faixa: str | None,
    storage: Mapping[str | None, dict[str, CategoryRate]],
    backup: Mapping[str | None, dict[str, CategoryRate]],
    cache: Mapping[str, dict[str, CategoryRate]],
) -> Decimal | None:
    """Taxa por unidade da parte no destino; sem motor, a maior entre os motores."""
    if tipo == CACHE:
        t = cache.get(faixa or "", {}).get(prov)
        return t.rate if t else None
    if tipo not in (ARMAZENAMENTO, BACKUP):
        return None
    por_motor = storage if tipo == ARMAZENAMENTO else backup

    def taxa(m: str | None) -> Decimal | None:
        t = por_motor.get(m, {}).get(prov) or por_motor.get(None, {}).get(prov)
        if t is None:
            return None
        return t.rate * (copias_do_storage(prov, m, ha) if tipo == ARMAZENAMENTO else 1)

    if motor_ is not None:
        return taxa(motor_)
    candidatas = [v for m in (None, *CATEGORIA_DO_MOTOR) if (v := taxa(m)) is not None]
    return max(candidatas) if candidatas else None


def _taxas_da_parte(
    pontos: list[Any], categoria: str, parte: Any, source_provider: str
) -> dict[str, CategoryRate]:
    return _taxas_por_provedor(
        pontos,
        categoria,
        source_provider=source_provider,
        zona=parte.zona,
        regiao_origem=parte.regiao_origem,
    )


def _taxas_do_resto(
    pontos: Mapping[str, list[Any]], parte: Any, source_provider: str
) -> dict[str, Any]:
    """Taxas de storage, backup e cache da zona da parte, no formato de `_resto_do_banco`."""

    def de(categoria: str) -> dict[str, CategoryRate]:
        return _taxas_da_parte(pontos.get(categoria, []), categoria, parte, source_provider)

    return {
        "storage": {
            None: de(CATEGORIA_STORAGE),
            **{m: de(c) for m, c in CATEGORIA_STORAGE_DO_MOTOR.items()},
        },
        "backup": {
            None: de(CATEGORIA_BACKUP),
            **{m: de(c) for m, c in CATEGORIA_BACKUP_DO_MOTOR.items()},
        },
        "cache": {f: de(c) for f, c in CATEGORIA_DA_FAIXA.items()},
    }


def _anexar_partes_do_banco(
    entradas: list[dict[str, Any]],
    detalhe: Mapping[str, Mapping[str, Decimal]],
    partes: Mapping[str, Mapping[str, Decimal]],
    *,
    instancias_na_origem: Decimal,
    converter: Any,
) -> None:
    """`partes` no equivalente de cada provedor: o custo de cada tipo de parte na origem
    e no destino (instâncias, cache, armazenamento, backup, sem equivalente, outros).
    Campo aditivo; mostra de onde vem a diferença do banco."""
    origem: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    origem["instancias"] = instancias_na_origem
    for chave, valores in partes.items():
        origem[ler_chave_da_parte(chave)[0]] += converter(valores.get("custo") or Decimal(0))
    for entrada in entradas:
        do_provedor = detalhe.get(entrada["provedor"])
        if do_provedor is None:
            continue
        resto = float(sum(do_provedor.values(), Decimal(0)))
        entrada["partes"] = {
            "origem": {k: round(float(v), 2) for k, v in origem.items()},
            "destino": {
                "instancias": round(entrada["custo_mensal_estimado"] - resto, 2),
                **{k: round(float(v), 2) for k, v in do_provedor.items()},
            },
        }


def _cotar_banco(
    *,
    source_provider: str,
    custo: Decimal,
    medido: Decimal,
    quantidade: Decimal,
    preco_efetivo: Decimal | None,
    taxas: dict[str, CategoryRate],
    fator_do_resto: Mapping[str, Decimal],
) -> list[CategoryEquivalent]:
    """Banco em duas partes (PLANO_PRECISAO_OCI P1.6, B1 a B4): a instância pela
    vCPU-hora x a taxa do destino; o resto (cache, armazenamento, backup, cobranças só
    da origem) pela razão que `_resto_do_banco` calculou para o destino. Sem vCPU-hora
    medida, a instância vai pela razão das taxas; destino sem a razão do resto fica com
    o resto ao custo da origem e a confiança baixa."""
    resto = max(custo - medido, Decimal(0))
    instancias = (
        estimate_by_quantity(
            source_provider=source_provider,
            source_cost=medido,
            quantity=quantidade,
            measured_cost=medido,
            effective_price=preco_efetivo,
            rates=taxas,
            unit=UNIDADE_DO_BANCO,
        )
        if preco_efetivo is not None and medido > 0
        else estimate_by_cost_ratio(
            source_provider=source_provider, source_cost=medido, rates=taxas
        )
    )
    out = []
    for e in instancias:
        fator = fator_do_resto.get(e.provider)
        total = e.estimated_monthly_cost + (resto * fator if fator is not None else resto)
        out.append(
            CategoryEquivalent(
                provider=e.provider,
                estimated_monthly_cost=total,
                cost_delta=total - custo,
                pricing_confidence=e.pricing_confidence if fator is not None else "low",
            )
        )
    return out


def _mais_fraca(a: str, b: str) -> str:
    """A mesma ordem de `category_calculator`: official > reference > low."""
    ordem = {"official": 3, "reference": 2, "low": 1}
    return a if ordem.get(a, 0) <= ordem.get(b, 0) else b


def _taxas_por_provedor(
    pontos: list[Any],
    categoria: str,
    *,
    source_provider: str,
    zona: str,
    regiao_origem: str | None,
) -> dict[str, CategoryRate]:
    """Uma taxa por provedor, da região escolhida para ele.

    A região vem primeiro (`regioes.escolher_regiao_de_preco`); dentro dela,
    `official` vence `reference`, e a captura mais recente desempata. Taxa
    oficial de outra região entra como `reference`.
    """
    por_provedor: dict[str, list[Any]] = {}
    for p in pontos:
        if p.currency == ANALYSIS_CURRENCY:
            por_provedor.setdefault(p.provider.lower(), []).append(p)
    taxas: dict[str, CategoryRate] = {}
    for prov, pts in sorted(por_provedor.items()):
        preferidas = [regiao_canonica(zona, prov)]
        if prov == source_provider.lower():
            preferidas.insert(0, regiao_origem)
        escolha = escolher_regiao_de_preco(
            prov, preferidas, {p.region for p in pts}, service_type=categoria
        )
        if escolha is None:
            continue
        melhor = max(
            (p for p in pts if p.region == escolha.regiao),
            key=lambda x: (
                _RANK_CONFIANCA.get(x.confidence, 0),
                str(x.captured_at or ""),
                x.sku_key,
            ),
        )
        confianca = melhor.confidence
        if escolha.aproximada and confianca == "official":
            confianca = "reference"
        taxas[prov] = CategoryRate(
            provider=prov,
            rate=melhor.price,
            unit=melhor.unit,
            confidence=confianca,
            region=escolha.regiao,
            approximate=escolha.aproximada,
        )
    return taxas


def _sem_repetir(itens: list[dict[str, str]]) -> list[dict[str, str]]:
    vistos = {tuple(sorted(i.items())): i for i in itens}
    return [vistos[k] for k in sorted(vistos)]


def _ledger_de_regioes(
    *,
    zona_por_pool: dict[str, str],
    pools_sem_zona: list[str],
    zona_categorias: str,
    regiao_origem_categorias: str | None,
    aproximadas: list[dict[str, str]],
) -> dict[str, Any]:
    pools_por_zona: dict[str, list[str]] = {}
    for pool, zona in sorted(zona_por_pool.items()):
        if pool not in pools_sem_zona:
            pools_por_zona.setdefault(zona, []).append(pool)
    ledger: dict[str, Any] = {
        "zona_padrao": ZONA_PADRAO,
        "pools_por_zona": pools_por_zona,
        "zona_categorias": zona_categorias,
        "regiao_origem_categorias": regiao_origem_categorias,
    }
    if pools_sem_zona:
        ledger["pools_sem_zona"] = sorted(pools_sem_zona)
    if aproximadas:
        ledger["cotacoes_aproximadas"] = aproximadas
    return ledger


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


# service types that require vCPU/RAM specs for equivalence matching
_COMPUTE_SERVICE_TYPES = frozenset({"virtual_server", "relational_database", "compute"})


class BuildMigrationContextUseCase:
    def __init__(  # noqa: PLR0917 -- existing dependency-injection contract
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingEnrichedRepository,
        metadata_repo: IServiceMetadataRepository,
        comparison_repo: IServiceComparisonRepository,
        sku_repo: ICloudSkuRepository | None = None,
        fx_repo: IExchangeRateRepository | None = None,
        price_repo: ICloudPricePointRepository | None = None,
    ) -> None:
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo
        self._matcher = EquivalenceMatcher(sku_repo, price_repo) if sku_repo else None
        self._aggregator = BillingAggregator(sku_repo) if sku_repo else None
        self._fx_repo = fx_repo
        self._price_repo = price_repo

    async def _pontos_do_banco(self) -> dict[str, list[Any]]:
        """Taxas do banco além da de `database`: por motor, storage, backup e cache."""
        assert self._price_repo is not None
        categorias = [
            *CATEGORIA_DO_MOTOR.values(),
            CATEGORIA_STORAGE,
            *CATEGORIA_STORAGE_DO_MOTOR.values(),
            CATEGORIA_BACKUP,
            *CATEGORIA_BACKUP_DO_MOTOR.values(),
            *CATEGORIA_DA_FAIXA.values(),
        ]
        return {c: await self._price_repo.rates_for_category(c) for c in categorias}

    async def execute(self, payload: BuildMigrationContextInput) -> MigrationContext:  # noqa: PLR0912, PLR0915
        report = await self._report_repo.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"report {payload.report_id} not found")
        if report.status != "done":
            raise ValueError(f"report {payload.report_id} status is {report.status}, expected done")
        if report.provider is None:
            raise ValueError(f"report {payload.report_id} has no provider set")

        source_provider = report.provider.value

        items = await listar_todos(
            self._item_repo.list_by_report, payload.report_id, payload.user_id
        )
        if not items:
            raise ValueError(f"report {payload.report_id} has no billing items")
        items = [_reviewed_billing_identity(item, source_provider) for item in items]

        # Bug fix: use cost-weighted dominant period, not first item's period.
        # A CSV can span two months; we want the one with the most spend.
        period_costs: dict[str, Decimal] = {}
        currency_counts: dict[str, int] = {}
        total_spend = Decimal("0")

        # ── 1. Aggregate by service ──────────────────────────────
        service_agg: dict[str, dict[str, Any]] = {}
        for item in items:
            total_spend += item.cost
            period_costs[item.billing_period] = (
                period_costs.get(item.billing_period, Decimal("0")) + item.cost
            )
            currency_counts[item.currency] = currency_counts.get(item.currency, 0) + 1
            key = item.service_name
            if key not in service_agg:
                service_agg[key] = {
                    "service_name": item.service_name,
                    "provider": item.provider,
                    "total_cost": Decimal("0"),
                    "currency": item.currency,
                    "line_count": 0,
                    "category": item.category,
                    "service_type": item.service_type,
                    "items": [],
                }
            service_agg[key]["total_cost"] += item.cost
            service_agg[key]["line_count"] += 1
            service_agg[key]["items"].append(item)

        billing_period = max(period_costs, key=lambda p: period_costs[p])
        currency = (
            max(currency_counts, key=lambda c: currency_counts[c]) if currency_counts else "USD"
        )

        # ── 2. Load source catalog ───────────────────────────────
        source_comparisons = await self._comparison_repo.list_by_provider(source_provider)
        source_metadata = await self._metadata_repo.list_by_providers([source_provider])
        meta_map = {_normalize(m.service_name): m for m in source_metadata}
        # Índice insensível a espaço/pontuação. Os parsers emitem nomes
        # concatenados (`AmazonRDS`) e o catálogo curado usa nomes com espaço
        # (`Amazon RDS`); sem esta ponte o casamento falha e o `service_type` cai
        # no palpite do classificador de SKU — foi assim que object storage virou
        # block storage. Lista (não dict) porque um serviço aparece uma vez por
        # capacidade (BigQuery = interactive_query + data_lake + data_warehouse).
        canonicals_squashed: dict[str, list[ServiceComparison]] = {}
        for c in sorted(source_comparisons, key=lambda x: (x.category, x.service_type)):
            key = service_key(source_provider, c.service_name)
            canonicals_squashed.setdefault(key, []).append(c)
        meta_squashed = {service_key(source_provider, m.service_name): m for m in source_metadata}

        # ── 3. Load target provider metadata ─────────────────────
        # `sorted`, não `list(set(...))`: a ordem de iteração de um set varia
        # entre processos (randomização de hash) e se propaga para a ordem dos
        # `equivalentes` de TODOS os pools do payload. Isso tornava a request
        # não-reproduzível e injetava ruído na entrada do modelo — dois runs do
        # mesmo CSV produziam a mesma verdade em ordem diferente.
        target_providers = sorted(
            {
                c.provider
                for c in await self._comparison_repo.list_all()
                if c.provider != source_provider
            }
        )
        target_metadata_all = await self._metadata_repo.list_by_providers(target_providers)
        target_meta_map: dict[tuple[str, str], Any] = {
            (m.provider.lower(), service_key(m.provider, m.service_name)): m.metadata
            for m in target_metadata_all
        }
        target_docs_map: dict[tuple[str, str], str | None] = {
            (m.provider, m.service_name): m.docs_costs for m in target_metadata_all
        }
        equivalents_cache: dict[tuple[str, str], list[EquivalentService]] = {}

        # Padrão de migração POR CLASSE, aprendido dos serviços já curados.
        # A dificuldade é propriedade do tipo (object storage é fácil em qualquer
        # nuvem; banco gerenciado é difícil em qualquer nuvem), então serviços sem
        # metadata própria herdam do tipo em vez de ficarem sem expectativa.
        _tipo_por_servico = {
            (c.provider, _normalize(c.service_name)): c.service_type
            for c in await self._comparison_repo.list_all()
        }
        class_defaults = learn_class_defaults(
            (
                _tipo_por_servico.get((m.provider, _normalize(m.service_name))),
                m.metadata,
            )
            for m in [*source_metadata, *target_metadata_all]
        )

        async def _get_equivalents(category: str, service_type: str) -> list[EquivalentService]:
            key = (category, service_type)
            if key not in equivalents_cache:
                raw = await self._comparison_repo.find_equivalents(category, source_provider)
                equivalents_cache[key] = [
                    EquivalentService(
                        provider=eq.provider,
                        service_name=eq.service_name,
                        category=category,
                        service_type=eq.service_type,
                        metadata=target_meta_map.get(
                            (eq.provider.lower(), service_key(eq.provider, eq.service_name))
                        ),
                        docs_costs=target_docs_map.get((eq.provider, eq.service_name)),
                    )
                    for eq in raw
                    if eq.service_type == service_type
                ]
            return equivalents_cache[key]

        # ── 4. Match helpers ─────────────────────────────────────
        # Apelidos aprovados pela curadoria da OneShift (decisão 2.11): o nome que a
        # fatura traz → a linha do DE-PARA. Só análises novas: o payload é congelado.
        aprovados = await self._comparison_repo.apelidos_aprovados(source_provider)

        def _canonical_name(name: str) -> str:
            aliases = PROVIDER_SERVICE_ALIASES.get(source_provider, {})
            if name in aliases:
                return aliases[name]
            return aprovados.get(service_key(source_provider, name), name)

        tipo_preferido = {
            service_key(source_provider, nome): tipo
            for nome, tipo in TIPO_PREFERIDO.get(source_provider, {}).items()
        }

        def _match(svc_name: str, hint_type: str | None = None) -> ServiceComparison | None:
            name = _canonical_name(svc_name)
            # Ponte concatenado <-> espaçado. Quando o serviço tem várias
            # capacidades, prefere a que bate com o tipo que o classificador
            # sugeriu; senão a primeira em ordem determinística.
            candidates = canonicals_squashed.get(service_key(source_provider, name))
            if candidates:
                # Sem o tipo do classificador, o tipo curado
                curado = tipo_preferido.get(service_key(source_provider, name))
                for preferido in (hint_type, curado):
                    for cand in candidates if preferido else ():
                        if cand.service_type == preferido:
                            return cand
                return candidates[0]
            return None

        def _meta_category(name: str) -> str | None:
            meta = meta_map.get(_normalize(name))
            return meta.category if meta else None

        # ── 5. Build old-style (categories) structure ────────────
        sorted_services = sorted(
            service_agg.values(),
            key=lambda x: x["total_cost"],
            reverse=True,
        )

        categories: dict[str, dict[str, Any]] = {}
        for raw in sorted_services:
            comp = _match(raw["service_name"], raw.get("service_type"))
            category_name = (
                comp.category
                if comp
                else (raw.get("category") or _meta_category(raw["service_name"]) or "unknown")
            )
            service_type = comp.service_type if comp else (raw.get("service_type") or "unknown")
            hint = service_type_hint(source_provider, raw["service_name"])
            if hint and comp is None:
                category_name, service_type = hint
            pelo_uso = None
            if comp is None and hint is None:
                # Nome que a fatura dá a mais de um serviço ("Storage", "Networking"):
                # o uso das linhas escolhe a entrada do DE-PARA (servico_pelo_uso)
                pelo_uso = servico_pelo_uso(
                    source_provider,
                    raw["service_name"],
                    ((i.usage_type, i.cost) for i in raw["items"]),
                )
                if pelo_uso is not None:
                    comp = _match(pelo_uso[0], raw.get("service_type"))
                    if comp is not None:
                        category_name, service_type = comp.category, comp.service_type

            # Missing quantities occur in aggregate exports too. Only explicit
            # financial identities/categories suppress functional equivalents.
            sem_uso = is_financial_component(raw["service_name"], category_name)
            if sem_uso:
                category_name = financial_category(raw["service_name"], category_name)

            equivs: list[EquivalentService] = []
            if not sem_uso and category_name != "unknown" and service_type != "unknown":
                equivs = await _get_equivalents(category_name, service_type)

            # Expectativa de migração (classe 6R + esforço) a partir da metadata
            # curada do serviço de ORIGEM. Instrumento financeiro não migra, então
            # não recebe expectativa. Derivação determinística no backend.
            svc_meta = meta_map.get(_normalize(_canonical_name(raw["service_name"])))
            if svc_meta is None:
                svc_meta = meta_squashed.get(
                    service_key(
                        source_provider,
                        _canonical_name(raw["service_name"]),
                    )
                )
            if svc_meta is None and comp is not None:
                # Nome que a fatura dá a mais de um serviço ("Storage") só acha a
                # metadata pela linha do DE-PARA que o uso escolheu.
                svc_meta = meta_squashed.get(service_key(source_provider, comp.service_name))
            expectation = None
            if not sem_uso:
                expectation = derive_expectation(svc_meta.metadata) if svc_meta else None
                if expectation is None:
                    # Herda o padrão do tipo (marcado como fonte="classe").
                    expectation = class_defaults.get(service_type)

            svc = BillingItemSummary(
                service_name=raw["service_name"],
                provider=raw["provider"],
                total_cost=raw["total_cost"],
                service_type=service_type,
                currency=raw["currency"],
                line_count=raw["line_count"],
                equivalents=equivs,
                migration_expectation=expectation.as_dict() if expectation else None,
                type_source=(
                    "catalog_alias"
                    if hint or (pelo_uso is not None and comp is not None)
                    else ("curado" if comp is not None else "inferido")
                ),
                migration_metadata=svc_meta.metadata if svc_meta and not sem_uso else None,
            )

            if category_name not in categories:
                categories[category_name] = {
                    "category": category_name,
                    "billing_total": Decimal("0"),
                    "services": [],
                }
            categories[category_name]["services"].append(svc)
            categories[category_name]["billing_total"] += svc.total_cost

        # ── 6. Build new-style (workloads) structure ─────────────
        workloads: list[WorkloadSummary] = []
        missing_sku_set: set[str] = set()
        if self._matcher is not None:
            sku_agg: dict[str, dict[str, Any]] = {}
            for item in items:
                sku_key = item.sku_id or item.service_name
                if sku_key not in sku_agg:
                    sku_agg[sku_key] = {
                        "sku_id": item.sku_id or item.service_name,
                        "usage_quantity": Decimal("0"),
                        "billed_cost": Decimal("0"),
                        "currency": item.currency,
                        "canonical_service": item.service_type or item.service_name,
                        "line_count": 0,
                    }
                sku_agg[sku_key]["usage_quantity"] += item.usage_amount or Decimal("0")
                sku_agg[sku_key]["billed_cost"] += item.cost
                sku_agg[sku_key]["line_count"] += 1

            for _sku_key, agg in sku_agg.items():
                matched = await self._matcher.match(
                    source_provider=source_provider,
                    sku_id=agg["sku_id"],
                    usage_quantity=agg["usage_quantity"],
                    billed_cost=agg["billed_cost"],
                    currency=agg["currency"],
                    canonical_service=agg["canonical_service"],
                    target_providers=target_providers,
                )

                has_specs = matched.source_specs is not None and (
                    matched.source_specs.vcpus is not None
                    or matched.source_specs.ram_gb is not None
                )
                if not has_specs and matched.canonical_service in _COMPUTE_SERVICE_TYPES:
                    missing_sku_set.add(agg["sku_id"])

                source_usage = SourceUsage(
                    sku_id=matched.sku_id,
                    usage_quantity=matched.usage_quantity,
                    billed_cost=matched.billed_cost,
                    currency=matched.currency,
                )

                equiv_items = []
                for eq in matched.equivalents:
                    _t = eq.technical_match
                    tm = (
                        TechnicalMatch(
                            vcpus=_t.vcpus,
                            ram_gb=_t.ram_gb,
                        )
                        if _t
                        else None
                    )
                    _f = eq.financials
                    fin = None
                    if _f is not None:
                        fin = FinancialEstimate(
                            unit_price=_f.unit_price,
                            estimated_monthly_cost=_f.estimated_monthly_cost,
                            cost_delta=_f.cost_delta,
                            pricing_confidence=_f.pricing_confidence,
                            price_source=getattr(_f, "price_source", None),
                            price_captured_at=getattr(_f, "price_captured_at", None),
                            price_lifecycle_state=getattr(_f, "price_lifecycle_state", None),
                            price_lifecycle_score=getattr(_f, "price_lifecycle_score", None),
                        )
                    equiv_items.append(
                        EquivalentService(
                            provider=eq.provider,
                            service_name=eq.service_name,
                            category=eq.category,
                            service_type=eq.service_type,
                            sku_id=getattr(eq, "sku_id", None),
                            technical_match=tm,
                            financials=fin,
                        )
                    )
                workloads.append(
                    WorkloadSummary(
                        canonical_service=matched.canonical_service,
                        source_usage=source_usage,
                        equivalents=equiv_items,
                    )
                )

        # ── 7. Aggregate into capacity pools ──────────────────────
        pools: tuple[WorkloadPool, ...] = ()
        auxiliary: AuxiliaryCosts | None = None
        mapping_confidence: dict[str, Decimal] = {}
        estimated_capacity_cost = Decimal("0")
        agg_result = None
        if self._aggregator is not None:
            agg_result = await self._aggregator.aggregate(items, source_provider)
            pools = agg_result.pools
            auxiliary = agg_result.auxiliary
            mapping_confidence = agg_result.confidence_breakdown
            estimated_capacity_cost = agg_result.estimated_capacity_cost

        # ── 8. Câmbio: normaliza custos para a moeda de análise ───
        # Nunca comparar BRL de origem com USD de catálogo (Precision Engine A1).
        fx = await self._resolve_fx(currency)

        # ── 9. Agrega pools por família ANTES de dimensionar ──────
        # Dimensionar projeto a projeto e somar depois produzia hardware de um
        # projeto com custo de dezenove. Agregando primeiro, o matcher vê a
        # capacidade real da família.
        family_pools = aggregate_by_family(pools)

        family_equivalents: dict[str, list[EquivalentService]] = {}
        pool_equivalents: dict[str, list[EquivalentService]] = {}
        database_equivalents: list[EquivalentService] = []

        # ── 9a. Regiões: onde cada pool roda e onde cada destino é cotado ──
        # Cada pool é cotado na zona dele (São Paulo cota São Paulo em todos os
        # provedores). Sem isto, todo bill era comparado com preço dos EUA.
        zona_por_pool: dict[str, str] = {}
        pools_sem_zona: list[str] = []
        for fpool in family_pools:
            zona = zona_da_regiao(source_provider, fpool.pool_key.region)
            if zona is None:
                pools_sem_zona.append(fpool.pool_key.as_str())
                zona = ZONA_PADRAO
            zona_por_pool[fpool.pool_key.as_str()] = zona
        cotacoes_aproximadas: list[dict[str, str]] = []

        # P1b: pool burstable cotado em destino sem crédito pela linha de base da CPU
        burstable_pela_base: list[dict[str, Any]] = []
        if self._matcher is not None and target_providers:
            curated_compute = await _get_equivalents("compute", "virtual_server")

            for fpool in family_pools:
                monthly_cost_analysis = convert(fpool.monthly_cost, fx) or Decimal("0")
                zona = zona_por_pool[fpool.pool_key.as_str()]
                perfil_do_pool = perfil(source_provider, fpool.pool_key.machine_family)
                if perfil_do_pool and perfil_do_pool.burstable and perfil_do_pool.base_cpu:
                    burstable_pela_base.append(
                        {
                            "pool": fpool.pool_key.as_str(),
                            "base_cpu": float(perfil_do_pool.base_cpu),
                            "vcpus_nominais": round(float(fpool.capacity.vcpus), 2),
                        }
                    )
                pedidas = {p: regiao_canonica(zona, p) for p in target_providers}
                l2_options = await self._matcher.match_pool(
                    vcpus=fpool.capacity.vcpus,
                    ram_gib=fpool.capacity.ram_gib,
                    monthly_cost=monthly_cost_analysis,
                    service_type="virtual_server",
                    target_providers=target_providers,
                    regioes={p: [r] for p, r in pedidas.items()},
                    perfil_origem=perfil_do_pool,
                )
                providers_with_l2: set[str] = set()
                equivs = []

                for opt in l2_options:
                    providers_with_l2.add(opt.provider.lower())
                    _fo = opt.financials
                    if _fo is not None and _fo.price_region_approximate:
                        cotacoes_aproximadas.append(
                            {
                                "provedor": opt.provider.lower(),
                                "pedida": str(pedidas.get(opt.provider) or "?"),
                                "cotada": str(_fo.price_region),
                                "uso": "compute",
                            }
                        )
                    # L3: enrich with curated metadata
                    meta = target_meta_map.get(
                        (opt.provider.lower(), service_key(opt.provider, opt.service_name))
                    )
                    docs = target_docs_map.get((opt.provider, opt.service_name))
                    fin = None
                    if opt.financials is not None:
                        fin = FinancialEstimate(
                            unit_price=opt.financials.unit_price,
                            estimated_monthly_cost=opt.financials.estimated_monthly_cost,
                            cost_delta=opt.financials.cost_delta,
                            pricing_confidence=opt.financials.pricing_confidence,
                            price_source=opt.financials.price_source,
                            price_captured_at=opt.financials.price_captured_at,
                            price_region=opt.financials.price_region,
                            price_region_approximate=opt.financials.price_region_approximate,
                            price_lifecycle_state=opt.financials.price_lifecycle_state,
                            price_lifecycle_score=opt.financials.price_lifecycle_score,
                        )
                    tm = None
                    if opt.technical_match is not None:
                        tm = TechnicalMatch(
                            vcpus=opt.technical_match.vcpus,
                            ram_gb=opt.technical_match.ram_gb,
                        )
                    equivs.append(
                        EquivalentService(
                            provider=opt.provider,
                            service_name=opt.service_name,
                            category=opt.category,
                            service_type=opt.service_type,
                            sku_id=opt.sku_id,
                            match_basis="pool_capacity",
                            instances=opt.instances,
                            metadata=meta,
                            docs_costs=docs,
                            technical_match=tm,
                            financials=fin,
                            architecture_mismatch=opt.architecture_mismatch,
                        )
                    )

                # L3-only curated fallback for providers without hardware match
                for ce in curated_compute:
                    if ce.provider.lower() in providers_with_l2:
                        continue
                    equivs.append(
                        EquivalentService(
                            provider=ce.provider,
                            service_name=ce.service_name,
                            category=ce.category,
                            service_type=ce.service_type,
                            match_basis="curated_only",
                            metadata=ce.metadata,
                            docs_costs=ce.docs_costs,
                        )
                    )

                family_equivalents[fpool.pool_key.as_str()] = equivs
                # Compat: expõe os mesmos equivalentes por projeto (drill-down
                # e formato legado). O dimensionamento continua sendo o da família.
                for proj in fpool.source_projects:
                    key = f"{proj}/{fpool.pool_key.machine_family}/{fpool.pool_key.region}"
                    pool_equivalents[key] = equivs

            # Database aux: L3 curated (no hardware sizing available at pool level)
            if auxiliary and auxiliary.database > 0:
                db_curated = await _get_equivalents("database", "relational_database")
                database_equivalents = list(db_curated)

        # ── 9b. Categorias não-compute: sizing por razão de taxa ──
        # Storage/egress não têm hardware; reprecificamos o custo (confiável) pela
        # razão de taxas por provedor. Provider-agnóstico, sem lógica por nuvem.
        category_equivalents: dict[str, list[dict[str, Any]]] = {}
        # Os custos auxiliares não são separados por região: a taxa é a da zona
        # dominante do bill (a de mais custo entre as linhas com região). A
        # origem usa a própria região quando houver taxa dela (PLANO D7).
        dominante = regiao_dominante(
            (source_provider, regiao_da_linha(source_provider, i.region, i.usage_type), i.cost)
            for i in items
        )
        zona_categorias, regiao_origem = dominante or (ZONA_PADRAO, None)
        if auxiliary is not None and self._price_repo is not None:
            aux_by_category = {
                "storage_ssd": auxiliary.storage_ssd,
                "storage_hdd": auxiliary.storage_hdd,
                "object_storage": auxiliary.object_storage,
                "network_egress": auxiliary.network_egress,
                "database": auxiliary.database,
                # Rede por tipo, cotada pela quantidade medida (PLANO_PRECO_NAO_COMPUTE)
                **{c: getattr(auxiliary, c) for c in UNIDADE_DA_REDE},
                # Plano de controle, segredos e chaves, pela quantidade medida (R4)
                "gke_control_plane": auxiliary.gke_control_plane,
                **{c: v for c, v in auxiliary.por_uso.items() if c in UNIDADE_POR_USO},
            }
            for category, aux_cost in aux_by_category.items():
                if aux_cost <= 0:
                    continue
                rate_points = await self._price_repo.rates_for_category(category)
                if not rate_points:
                    continue
                # Banco: taxas por motor, de storage, de backup e de cache (PLANO_PRECISAO_OCI)
                e_banco = category == CATEGORIA_BANCO
                pesos_motor = auxiliary.banco_por_motor if e_banco else {}
                pontos_banco = await self._pontos_do_banco() if e_banco else {}
                # P1: cada parte da categoria na taxa da zona dela, somadas. Sem região na
                # linha, a parte vai para a zona dominante (fração declarada em `zonas`).
                partes = dividir_por_zona(
                    source_provider,
                    auxiliary.por_regiao.get(category) or {"": aux_cost},
                    zona_dominante=zona_categorias,
                    regiao_dominante=regiao_origem,
                    quantidade_por_regiao=auxiliary.quantidade_por_regiao.get(category),
                    medido_por_regiao=auxiliary.medido_por_regiao.get(category),
                )
                # Rede: a quantidade medida dispensa a taxa da origem. O preço efetivo
                # (custo medido / quantidade) converte o custo das linhas sem quantidade.
                unidade_medida = (
                    UNIDADE_DA_REDE.get(category)
                    or UNIDADE_POR_USO.get(category)
                    or (UNIDADE_DO_BANCO if category == CATEGORIA_BANCO else None)
                )
                quantidade_total = sum((p.quantidade for p in partes), Decimal("0"))
                medido_total = convert(sum((p.custo_medido for p in partes), Decimal("0")), fx)
                preco_efetivo = (
                    medido_total / quantidade_total
                    if unidade_medida and medido_total and quantidade_total > 0
                    else None
                )
                fator_do_resto: dict[str, Decimal] = {}
                detalhe_do_resto: dict[str, dict[str, Decimal]] = {}
                if e_banco and partes:
                    fator_do_resto, detalhe_do_resto = _resto_do_banco(
                        auxiliary.banco_partes,
                        custos={
                            k: convert(v["custo"], fx) or Decimal(0)
                            for k, v in auxiliary.banco_partes.items()
                        },
                        **_taxas_do_resto(pontos_banco, partes[0], source_provider),
                        provedores=sorted(
                            {p.provider.lower() for p in rate_points} - {source_provider.lower()}
                        ),
                    )
                # Só avisa o que um sync corrige: taxa oficial do provedor
                # existe, mas não na zona. Taxa que só existe como referência
                # (banco, disco da Azure) não tem fonte regional para buscar.
                com_oficial = {
                    p.provider.lower() for p in rate_points if p.confidence == "official"
                }
                cotadas: list[tuple[Any, dict[str, CategoryRate], list[Any], Decimal]] = []
                # Banco: a cotação em cada motor aberto, o preço de cada opção do destino
                # (PLANO_EQUIVALENTES_E_TARIFAS §6). O total segue a mistura medida
                por_motor_no_destino: dict[str, dict[str, Decimal]] = defaultdict(
                    lambda: defaultdict(Decimal)
                )
                for parte in partes:
                    rates = _taxas_por_provedor(
                        rate_points,
                        category,
                        source_provider=source_provider,
                        zona=parte.zona,
                        regiao_origem=parte.regiao_origem,
                    )
                    for prov, taxa in rates.items():
                        if taxa.approximate and prov in com_oficial:
                            cotacoes_aproximadas.append(
                                {
                                    "provedor": prov,
                                    "pedida": str(
                                        (parte.regiao_origem if prov == source_provider else None)
                                        or regiao_canonica(parte.zona, prov)
                                        or "?"
                                    ),
                                    "cotada": str(taxa.region),
                                    "uso": category,
                                }
                            )
                    custo_da_parte = convert(parte.custo, fx) or Decimal("0")
                    medido_da_parte = convert(parte.custo_medido, fx) or Decimal("0")
                    da_parte: list[CategoryEquivalent]
                    if e_banco:
                        taxas_dos_motores = {
                            m: _taxas_da_parte(pontos_banco.get(c, []), c, parte, source_provider)
                            for m, c in CATEGORIA_DO_MOTOR.items()
                        }
                        cotar = partial(
                            _cotar_banco,
                            source_provider=source_provider,
                            custo=custo_da_parte,
                            medido=medido_da_parte,
                            quantidade=parte.quantidade,
                            preco_efetivo=preco_efetivo,
                            fator_do_resto=fator_do_resto,
                        )
                        for m in CATEGORIA_DO_MOTOR:
                            no_motor = _misturar_motores(
                                rates, taxas_dos_motores, pesos_no_motor(pesos_motor, m)
                            )
                            for cotado in cotar(taxas=no_motor):
                                por_motor_no_destino[cotado.provider][m] += (
                                    cotado.estimated_monthly_cost
                                )
                        rates = _misturar_motores(rates, taxas_dos_motores, pesos_motor)
                        da_parte = cotar(taxas=rates)
                    elif preco_efetivo is not None and unidade_medida:
                        da_parte = estimate_by_quantity(
                            source_provider=source_provider,
                            source_cost=custo_da_parte,
                            quantity=parte.quantidade,
                            measured_cost=medido_da_parte,
                            effective_price=preco_efetivo,
                            rates=rates,
                            unit=unidade_medida,
                            # A franquia é mensal e da conta toda: cada zona fica com a
                            # parte dela pelos GB medidos (pelo custo, sem medida)
                            franquia=(
                                {
                                    p: g
                                    * (
                                        parte.quantidade / quantidade_total
                                        if quantidade_total > 0
                                        else parte.fracao
                                    )
                                    for p, g in FRANQUIA_DE_EGRESS_GB.items()
                                }
                                if category == CATEGORIA_EGRESS
                                else None
                            ),
                        )
                    else:
                        da_parte = estimate_by_cost_ratio(
                            source_provider=source_provider,
                            source_cost=custo_da_parte,
                            rates=rates,
                        )
                    cotadas.append((parte, rates, da_parte, custo_da_parte))
                if not cotadas or not cotadas[0][2]:
                    continue
                # A parte maior dá as taxas que a resposta mostra; a soma dá o custo
                _, rates, principais, _ = cotadas[0]
                somas = _somar_partes(cotadas)
                zonas = (
                    [
                        {
                            "zona": parte.zona,
                            "fracao": round(float(parte.fracao), 4),
                            "regiao_origem": parte.regiao_origem,
                            **(
                                {"sem_regiao": round(float(parte.sem_regiao / parte.custo), 4)}
                                if parte.sem_regiao > 0
                                else {}
                            ),
                        }
                        for parte, *_ in cotadas
                    ]
                    if len(cotadas) > 1
                    else None
                )
                taxa_da_origem = rates.get(source_provider.lower())
                origem: dict[str, Any] = (
                    {
                        "metodo": "quantidade_medida",
                        "quantidade": round(float(quantidade_total), 4),
                        "taxa_origem": str(round(preco_efetivo, 6)),
                        "taxa_origem_regiao": None,
                    }
                    if preco_efetivo is not None
                    else {
                        "metodo": "category_rate_ratio",
                        "taxa_origem": str(taxa_da_origem.rate) if taxa_da_origem else None,
                        "taxa_origem_regiao": taxa_da_origem.region if taxa_da_origem else None,
                        **(
                            {"taxa_origem_aproximada": True}
                            if taxa_da_origem and taxa_da_origem.approximate
                            else {}
                        ),
                    }
                )
                category_equivalents[category] = [
                    {
                        "provedor": e.provider,
                        "custo_mensal_estimado": round(float(somas[e.provider][0]), 2),
                        "delta_custo": round(float(somas[e.provider][1]), 2),
                        "confianca_preco": somas[e.provider][2],
                        **origem,
                        "taxa_destino": str(rates[e.provider].rate),
                        "unidade": rates[e.provider].unit,
                        "categoria": category,
                        "taxa_destino_regiao": rates[e.provider].region,
                        **(
                            {"taxa_destino_aproximada": True}
                            if rates[e.provider].approximate
                            else {}
                        ),
                        **({"zonas": zonas} if zonas else {}),
                        **(
                            {
                                "por_motor": {
                                    m: round(float(v), 2)
                                    for m, v in por_motor_no_destino[e.provider].items()
                                }
                            }
                            if e.provider in por_motor_no_destino
                            else {}
                        ),
                    }
                    for e in principais
                ]
                if e_banco and detalhe_do_resto:
                    _anexar_partes_do_banco(
                        category_equivalents[category],
                        detalhe_do_resto,
                        auxiliary.banco_partes,
                        instancias_na_origem=medido_total or Decimal(0),
                        converter=lambda v: convert(v, fx) or Decimal(0),
                    )

        # ── 10. Ledger de premissas (auditoria) ───────────────────
        mapped_cost = total_spend - (auxiliary.orphan if auxiliary else Decimal("0"))
        cobertura = float(mapped_cost / total_spend) if total_spend > 0 else 0.0
        # Confiança do preço é POR-PROVEDOR: 'official' quando veio de
        # cloud_price_points (API oficial), 'low' quando do catálogo seed.
        confianca_por_provedor: dict[str, str] = {}
        for equivs in family_equivalents.values():
            for e in equivs:
                if e.financials is not None:
                    confianca_por_provedor[e.provider.lower()] = e.financials.pricing_confidence
        # Cobertura de ANÁLISE: fração do gasto que tem equivalentes cross-cloud
        # (compute + categorias reprecificadas), vs. classificação apenas. É o que
        # mede o avanço da Wave 4: sair de balde opaco para comparação.
        analyzed = sum(
            (convert(fp.monthly_cost, fx) or Decimal("0") for fp in family_pools),
            Decimal("0"),
        )
        for category in category_equivalents:
            custo_da_categoria = auxiliary.custo(category) if auxiliary else Decimal("0")
            analyzed += convert(custo_da_categoria, fx) or Decimal("0")
        total_analysis = convert(total_spend, fx) or Decimal("0")
        cobertura_analise = float(analyzed / total_analysis) if total_analysis > 0 else 0.0

        # ── 9c. Consolidação por provedor (backend calcula, IA narra) ──
        # Cada provedor só reprecifica o que ELE cobre; o resto fica ao custo atual.
        _tally: dict[str, dict[str, Any]] = {}
        # Custo atual (origem) agrupado pelo rótulo de confiança do PREÇO que o cotou.
        # É o insumo da confiança da análise (domain/services/confidence.py): "que
        # fração do custo atual foi cotada com preço oficial?" (lote v4, V4.7).
        _custo_por_confianca: dict[str, Decimal] = {}

        def _add(provider: str, analyzed_cost: Decimal, source_cost: Decimal, conf: str) -> None:
            acc = _tally.setdefault(
                provider,
                {"analyzed": Decimal("0"), "covered": Decimal("0"), "confs": []},
            )
            acc["analyzed"] += analyzed_cost
            acc["covered"] += source_cost
            acc["confs"].append(conf)
            # V4.7: pesa pelo custo ATUAL que a cotação cobre, não pelo valor cotado
            # no destino. Senão uma cotação mais barata (e mais precisa) com preço
            # oficial baixava a confiança da análise.
            _custo_por_confianca[conf] = _custo_por_confianca.get(conf, Decimal("0")) + source_cost

        family_src = {
            fp.pool_key.as_str(): convert(fp.monthly_cost, fx) or Decimal("0")
            for fp in family_pools
        }
        family_vcpus = {fp.pool_key.as_str(): fp.capacity.vcpus for fp in family_pools}
        vcpus_cotados: dict[str, Decimal] = {}
        for fkey, equivs in family_equivalents.items():
            src = family_src.get(fkey, Decimal("0"))
            chosen: dict[str, EquivalentService] = {}
            for e in sorted(
                equivs,
                key=lambda e: (
                    e.financials.estimated_monthly_cost
                    if e.financials and e.financials.estimated_monthly_cost is not None
                    else Decimal("Infinity"),
                    e.sku_id or "",
                    e.service_name,
                ),
            ):
                fin = e.financials
                if fin is not None and fin.estimated_monthly_cost is not None:
                    if e.provider.lower() in chosen:
                        continue
                    chosen[e.provider.lower()] = e
                    _add(
                        e.provider.lower(),
                        fin.estimated_monthly_cost,
                        src,
                        fin.pricing_confidence,
                    )
                    vcpus_cotados[e.provider.lower()] = vcpus_cotados.get(
                        e.provider.lower(), Decimal("0")
                    ) + family_vcpus.get(fkey, Decimal("0"))
        # O compromisso que paga máquina virtual (Savings Plan de compute, reserva
        # de EC2, CUD) sai da origem junto com os pools: o destino foi cotado
        # on-demand pelas horas medidas, inclusive as que o compromisso pagava.
        # Sem isto ele ficava no saldo mantido ao custo atual e o destino pagava
        # as horas e a taxa do plano. Proporcional à capacidade que o provedor
        # cotou: quem não cotou um pool não ganha o crédito dele.
        compromisso_compute = (
            convert(auxiliary.commitment_compute, fx) or Decimal("0")
            if auxiliary is not None
            else Decimal("0")
        )
        vcpus_dos_pools = sum(family_vcpus.values(), Decimal("0"))
        compromisso_por_provedor: dict[str, Decimal] = {}
        if compromisso_compute > 0 and vcpus_dos_pools > 0:
            for provider, vcpus in vcpus_cotados.items():
                parte = compromisso_compute * min(Decimal("1"), vcpus / vcpus_dos_pools)
                compromisso_por_provedor[provider] = parte
                _tally[provider]["covered"] += parte
        for category, cat_equivs in category_equivalents.items():
            custo_da_categoria = auxiliary.custo(category) if auxiliary else Decimal("0")
            src = convert(custo_da_categoria, fx) or Decimal("0")
            for ce in cat_equivs:
                _add(
                    str(ce["provedor"]).lower(),
                    Decimal(str(ce["custo_mensal_estimado"])),
                    src,
                    str(ce["confianca_preco"]),
                )

        tallies = {
            provider: ProviderTally(
                analyzed_cost=acc["analyzed"],
                source_covered=acc["covered"],
                confidence=weakest_confidence(acc["confs"]),
            )
            for provider, acc in _tally.items()
        }
        resumo_executivo = consolidate(
            source_provider=source_provider,
            current_total=total_analysis,
            tallies=tallies,
        )

        # ── 10b. Alertas do backend contra os próprios insumos ────
        # O agente pode contestar um número nosso; aqui o backend contesta a si
        # mesmo. Sem isto, um bill sem taxa de câmbio era convertido por 1 —
        # somando moedas diferentes — e o rótulo `SEM_TAXA_CAMBIO` morria no
        # ledger sem que ninguém lesse.
        alertas = [a for a in (avaliar_cambio(fx, datetime.now(UTC).date()),) if a]
        alertas += avaliar_regioes(
            sem_zona=pools_sem_zona,
            zona_padrao=ZONA_PADRAO,
            aproximadas=_sem_repetir(cotacoes_aproximadas),
        )
        confianca = avaliar_confianca(_custo_por_confianca).com_rebaixamento(
            [a.titulo for a in alertas if a.rebaixa]
        )

        # Pools com capacidade medida e custo zero: o bill cobra aquelas
        # instâncias na linha do compromisso (Savings Plan, RI, CUD), não na
        # delas. A economia calculada para esses pools sai superestimada, porque
        # o comparativo assume origem de custo zero. Declarar é o que temos —
        # ratear o compromisso entre pools seria estimativa vestida de fato.
        cobertura_compromisso = avaliar_cobertura(
            {fp.pool_key.as_str(): (fp.capacity.vcpus, fp.monthly_cost) for fp in family_pools},
            auxiliary.commitment if auxiliary is not None else Decimal("0"),
        )
        # V4.6: pool que paga bem menos que o próprio preço sob demanda, num bill com
        # compromisso de compute, tem parte das horas paga na linha do compromisso. A
        # comparação pool a pool não vale para ele (o C7G saía a 7-8x); o total já tira
        # o compromisso junto com os pools.
        coberto_estimado = await self._cobertura_estimada(
            source_provider, family_pools, auxiliary, fx
        )

        premissas: dict[str, Any] = {
            "fx": as_ledger(fx),
            "moeda_origem": currency,
            "moeda_analise": ANALYSIS_CURRENCY,
            "janela_horas": float(family_pools[0].hours_window) if family_pools else 730.0,
            "fonte_precos": "cloud_price_points (oficial) + seed_catalog_pareto (fallback)",
            "confianca_precos": confianca_por_provedor or "low",
            # Confiança DA ANÁLISE, medida em vez de opinada: razão do custo
            # cotado com preço oficial sobre o custo total cotado. Antes esse
            # nível vinha do LLM — um modelo opinando sobre a qualidade dos dados
            # que ele próprio recebeu.
            "confianca_analise": confianca.as_ledger(),
            **(
                {
                    "compromisso_estimado_por_pool": {
                        "pools": {k: float(v) for k, v in sorted(coberto_estimado.items())},
                        "explicacao": (
                            "Fração do preço sob demanda da própria origem que o pool não "
                            "pagou na linha dele: o compromisso de compute cobriu parte das "
                            "horas. A comparação pool a pool não vale para eles; o total do "
                            "cenário já tira o compromisso junto com os pools."
                        ),
                    }
                }
                if coberto_estimado
                else {}
            ),
            # Sugestões que o backend levanta sozinho. Vão para a mesma lista de
            # tela que as do agente, com `origem: backend` — quem lê precisa
            # saber que existe uma dúvida, não quem a levantou.
            "sugestoes_backend": [a.as_sugestao() for a in alertas],
            "cobertura_classificacao": cobertura,
            "cobertura_analise": cobertura_analise,
            # Só aparece quando existe — um bill sem compromisso não precisa
            # carregar a explicação de um problema que ele não tem.
            **(
                {"cobertura_por_compromisso": cobertura_compromisso.as_ledger()}
                if cobertura_compromisso.existe
                else {}
            ),
            "fator_utilizacao": 1.0,
            **(
                {
                    "burstable_pela_base": {
                        "pools": burstable_pela_base,
                        "explicacao": (
                            "Máquina burstable (CPU com crédito) entrega sem crédito só a linha "
                            "de base da vCPU. Em destino sem burstable, estes pools foram "
                            "dimensionados pela base, não pela vCPU nominal; em destino com "
                            "burstable, pela nominal. Vence o mais barato. A memória não muda."
                        ),
                    }
                }
                if burstable_pela_base
                else {}
            ),
            # Como o compute foi cotado: pela capacidade-hora medida, não por
            # máquina inteira ligada o mês todo (PLANO_EMPACOTAMENTO).
            "cotacao_compute": {
                "base": "capacidade_hora",
                "explicacao": (
                    "Cada pool é cotado pelas horas de vCPU e de memória que rodou "
                    "na origem: preço da instância de destino x 730 h x a fração "
                    "dela que o pool ocupa. O número de instâncias é o "
                    "dimensionamento para provisionar, arredondado para cima. "
                    "Uma máquina pequena ligada o mês inteiro pode custar até "
                    "uma instância inteira no destino."
                ),
            },
            # Só aparece quando existe: o compromisso que paga máquina virtual
            # saiu da origem junto com os pools (domain/services/compromisso_de_compute).
            **(
                {
                    "compromisso_de_compute": {
                        "valor": round(float(compromisso_compute), 2),
                        "por_provedor": {
                            k: round(float(v), 2)
                            for k, v in sorted(compromisso_por_provedor.items())
                        },
                        "explicacao": (
                            "Savings Plan, reserva ou CUD que paga horas de "
                            "máquina virtual. O destino foi cotado on-demand "
                            "pelas horas medidas, inclusive as que o compromisso "
                            "pagava; por isso o compromisso sai da origem junto "
                            "com os pools, na proporção da capacidade que cada "
                            "provedor cotou, em vez de ficar no saldo mantido ao "
                            "custo atual. Comprar compromisso no destino reduz o "
                            "custo cotado. Pool a pool, o custo de origem segue "
                            "líquido do compromisso e não é comparável sozinho."
                        ),
                    }
                }
                if compromisso_compute > 0
                else {}
            ),
            # Onde cada coisa foi cotada (PLANO_PRECO_REGIONAL). Sem isto um
            # número de São Paulo e um da Virgínia são indistinguíveis.
            "regioes": _ledger_de_regioes(
                zona_por_pool=zona_por_pool,
                pools_sem_zona=pools_sem_zona,
                zona_categorias=zona_categorias,
                regiao_origem_categorias=regiao_origem,
                aproximadas=_sem_repetir(cotacoes_aproximadas),
            ),
            # Fração do gasto de compute cuja capacidade foi estimada por shape
            # (instance-hours → vCPU/RAM) em vez de faturada por-vCPU.
            "dimensionamento_estimado": (
                float(convert(estimated_capacity_cost, fx) or Decimal("0"))
                / float(convert(total_spend, fx) or Decimal("1"))
                if total_spend > 0
                else 0.0
            ),
        }

        # ── 11. Reconciliação: a análise auditando a si mesma ─────
        # Roda sempre; grava violações/suspeitas em premissas. É o gate que
        # transforma "achei um bug lendo o CSV" em "a API sabe onde não confia".
        # Fração do gasto cujo tipo é palpite do classificador, não catálogo curado.
        custo_inferido = sum(
            (
                sv.total_cost
                for c in categories.values()
                for sv in c["services"]
                if getattr(sv, "type_source", "curado") != "curado"
            ),
            Decimal("0"),
        )
        fracao_inferida = float(custo_inferido / total_spend) if total_spend > 0 else 0.0
        premissas["tipo_servico_inferido"] = fracao_inferida

        # ── 10c. Export detalhado: o que foi medido e em que base ─────
        # Só existe com export detalhado; o bill agregado não ganha as chaves.
        if agg_result is not None and agg_result.inventario is not None:
            base = base_de_custo(agg_result.custo_por_base)
            if base:
                premissas["base_de_custo"] = base
            desconto = desconto_origem_sobre_lista(
                agg_result.custo_sob_demanda, agg_result.custo_efetivo_referenciado
            )
            if desconto:
                premissas["desconto_origem_sobre_lista"] = desconto
            premissas["dimensionamento_medido"] = fracao(
                agg_result.measured_capacity_cost, total_spend
            )

        premissas["reconciliacao"] = reconcile(
            total_spend=total_spend,
            family_pools=family_pools,
            auxiliary=auxiliary,
            family_equivalents=family_equivalents,
            target_providers=target_providers,
            cobertura_classificacao=cobertura,
            fx=fx,
            fracao_tipo_inferido=fracao_inferida,
        )

        return MigrationContext(
            source_provider=source_provider,
            billing_period=billing_period,
            total_spend=total_spend,
            currency=currency,
            categories=[
                ServiceCategoryGroup(
                    category=c["category"],
                    billing_total=c["billing_total"],
                    services=c["services"],
                )
                for c in categories.values()
            ],
            workloads=workloads,
            pools=pools,
            auxiliary=auxiliary,
            missing_sku_ids=tuple(sorted(missing_sku_set)),
            mapping_confidence=mapping_confidence,
            pool_equivalents=pool_equivalents,
            database_equivalents=database_equivalents,
            family_pools=family_pools,
            family_equivalents=family_equivalents,
            category_equivalents=category_equivalents,
            analysis_currency=ANALYSIS_CURRENCY,
            premissas=premissas,
            resumo_executivo=resumo_executivo,
            inventario=agg_result.inventario if agg_result is not None else None,
            coberto_por_compromisso_estimado=coberto_estimado,
        )

    async def _cobertura_estimada(
        self,
        provider: str,
        family_pools: Sequence[FamilyPool],
        auxiliary: AuxiliaryCosts | None,
        fx: FxRate,
    ) -> dict[str, Decimal]:
        """Pool → fração do próprio preço sob demanda que o compromisso pagou (V4.6).

        Só num bill com compromisso de compute, e só com preço oficial da família na
        região do pool (a mediana por vCPU-hora). Sem isso, não diz nada."""
        if self._price_repo is None or auxiliary is None or auxiliary.commitment_compute <= 0:
            return {}
        pontos_por_regiao: dict[str, list[Any]] = {}
        resultado: dict[str, Decimal] = {}
        for fp in family_pools:
            vcpu_horas = fp.capacity.vcpus * fp.hours_window
            custo = convert(fp.monthly_cost, fx) or Decimal("0")
            regiao = fp.pool_key.region
            if vcpu_horas <= 0 or custo <= 0 or not regiao or regiao == "global":
                continue
            if regiao not in pontos_por_regiao:
                pontos_por_regiao[regiao] = await self._price_repo.find(
                    provider=provider, service_type="virtual_server", region=regiao
                )
            familia = fp.pool_key.machine_family.upper()
            taxas = sorted(
                p.price / p.vcpus
                for p in pontos_por_regiao[regiao]
                if p.vcpus
                and p.vcpus > 0
                and p.price > 0
                and (familia_do_ponto(provider, p.sku_key) or "").upper() == familia
            )
            if not taxas:
                continue
            fracao = cobertura_estimada(custo / vcpu_horas, taxas[len(taxas) // 2])
            if fracao is not None and fracao >= LIMIAR_DE_COBERTURA:
                resultado[fp.pool_key.as_str()] = fracao.quantize(Decimal("0.0001"))
        return resultado

    async def _resolve_fx(self, source_currency: str) -> FxRate:
        """Taxa origem→moeda de análise: tabela primeiro, config como fallback."""
        src = (source_currency or ANALYSIS_CURRENCY).upper()
        if src == ANALYSIS_CURRENCY:
            return identity_rate(src)

        par = f"{src}/{ANALYSIS_CURRENCY}"
        if self._fx_repo is not None:
            rate = await self._fx_repo.get_latest(par)
            if rate is not None:
                return rate

        settings = get_settings()
        configured = getattr(settings, f"fx_{src.lower()}_{ANALYSIS_CURRENCY.lower()}", None)
        if configured:
            return FxRate(par=par, taxa=Decimal(str(configured)), fonte="config")

        # Sem taxa conhecida: identidade seria comparar moedas diferentes.
        # Preferimos sinalizar do que devolver número errado — `avaliar_cambio`
        # transforma esse rótulo em rebaixamento de confiança e aviso na tela.
        return taxa_desconhecida(par)
