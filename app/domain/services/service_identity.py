"""Versioned billing identities; a missing usage quantity is not a financial charge."""

from app.domain.services.sku_normalization import squash_key

_FINANCIAL_CATEGORIES = frozenset(
    {
        "tax",
        "taxes",
        "imposto",
        "impostos",
        "commitment",
        "compromisso",
        "discount",
        "credits",
        "credit",
        "support",
    }
)
_FINANCIAL_PREFIXES = (
    "savingsplan",
    "computesavingsplan",
    "ec2instancesavingsplan",
    "committedusediscount",
    "awssupport",
    "amazonsupport",
)
_ALIASES = {
    "aws": {
        "amazonecr": "amazonecr",
        "amazonelasticcontainerregistry": "amazonecr",
        "amazonelasticcontainerregistryecr": "amazonecr",
        "amazonelasticcontainerregistrypublic": "amazonecr",
        "elasticcontainerregistrypublic": "amazonecr",
        "ec2containerregistryecr": "amazonecr",
        "ec2containerregistry": "amazonecr",
        "amazonglacier": "amazons3glacier",
        "amazons3glacier": "amazons3glacier",
        "amazoneks": "amazoneks",
        "amazonelastickubernetesserviceeks": "amazoneks",
        "amazonelastickubernetesservice": "amazoneks",
        "amazonelasticcontainerserviceforkubernetes": "amazoneks",
    },
}


def service_key(provider: str, name: str) -> str:
    stem, separator, suffix = name.rpartition("(")
    acronym = suffix.removesuffix(")")
    if separator and suffix.endswith(")") and acronym.isalnum() and acronym.isupper():
        name = stem
    key = squash_key(name)
    return _ALIASES.get(provider.lower(), {}).get(key, key)


# Prefixo de marca que a fatura e o DE-PARA escrevem de um jeito ou de outro: "Event Grid"
# (MeterCategory da Azure) e "Azure Event Grid"; "Cloud Dataflow" e "Dataflow";
# "AmazonBedrock" (código do CUR) e "AWS Bedrock" (PLANO_CONFERENCIA_DE_SERVICOS C6)
_MARCAS: dict[str, tuple[str, ...]] = {
    "aws": ("amazon", "aws"),
    "azure": ("microsoftazure", "microsoft", "azure"),
    "gcp": ("googlecloud", "google", "cloud"),
    "oci": ("oraclecloudinfrastructure", "oracle", "oci"),
}
# Sem o prefixo, o resto precisa dizer o que é (não "q" de "AmazonQ")
_RESTO_MINIMO = 3


def variantes_de_marca(provider: str, chave: str) -> list[str]:
    """A chave sem o prefixo de marca e com cada um dos outros, para quando o nome exato
    não está no DE-PARA. Ordem fixa: o nome sem marca primeiro."""
    marcas = _MARCAS.get(provider.lower(), ())
    resto = next(
        (
            chave[len(m) :]
            for m in marcas
            if chave.startswith(m) and len(chave) - len(m) >= _RESTO_MINIMO
        ),
        chave,
    )
    return [v for v in (resto, *(m + resto for m in marcas)) if v != chave]


def service_type_hint(provider: str, name: str) -> tuple[str, str] | None:
    """Narrow, reviewed aliases override broad SKU guesses, never arbitrary substrings."""
    return {
        ("aws", "amazonecr"): ("containers", "container_registry"),
        ("aws", "amazons3glacier"): ("storage", "archive_cold_storage"),
        ("aws", "amazoneks"): ("containers", "managed_kubernetes"),
    }.get((provider.lower(), service_key(provider, name)))


def is_financial_component(name: str, category: str = "") -> bool:
    key = squash_key(name)
    return (
        category.lower() in _FINANCIAL_CATEGORIES
        or key in {"tax", "taxes", "credit", "credits", "refund", "refunds"}
        or key.startswith(_FINANCIAL_PREFIXES)
    )


def financial_category(name: str, category: str = "") -> str:
    if category.lower() in _FINANCIAL_CATEGORIES:
        return category.lower()
    key = squash_key(name)
    if key in {"tax", "taxes"}:
        return "tax"
    if key in {"credit", "credits", "refund", "refunds"}:
        return "credits"
    if "support" in key:
        return "support"
    return "commitment"
