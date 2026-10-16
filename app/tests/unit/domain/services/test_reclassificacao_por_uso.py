import pytest

from app.domain.services.reclassificacao_por_uso import SEM_COTACAO, reclassificar


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "antes", "depois"),
    [
        ("aws", "AmazonRDS", "HeavyUsage:db.r8g.2xl", "core", "database"),
        ("aws", "AmazonElastiCache", "HeavyUsage:cache.t4g.medium", "core", "database"),
        ("aws", "AmazonRDS", "HeavyUsage:db.t4g.medium", "database", "database"),
        (
            "aws",
            "Amazon Managed Streaming for Apache Kafka",
            "USE1-Kafka.m5.large",
            "database",
            SEM_COTACAO,
        ),
        ("aws", "AmazonMSK", "USE1-Kafka.Storage.GP2", "database", SEM_COTACAO),
        # O tráfego do MSK segue a rede
        (
            "aws",
            "Amazon Managed Streaming for Apache Kafka",
            "DataTransfer-Regional-Bytes",
            "network_egress",
            "network_egress",
        ),
        ("gcp", "Source Repository", "Storage", "storage_hdd", "object_storage"),
        # O mesmo SKU em outro serviço não muda
        ("gcp", "Cloud Storage", "Storage", "object_storage", "object_storage"),
        ("aws", "AmazonEC2", "BoxUsage:c7g.large", "core", "core"),
        ("azure", "Virtual Machines", "D4s v5", "core", "core"),
    ],
)
def test_reclassificar(provedor, servico, uso, antes, depois) -> None:
    assert reclassificar(provedor, servico, uso, antes) == depois
