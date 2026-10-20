"""Versioned billing identities; a missing usage quantity is not a financial charge."""

from app.domain.services.sku_normalization import squash_key

_FINANCIAL_CATEGORIES = frozenset({
    "tax", "taxes", "imposto", "impostos", "commitment", "compromisso",
    "discount", "credits", "credit", "support",
})
_FINANCIAL_PREFIXES = (
    "savingsplan", "computesavingsplan", "ec2instancesavingsplan",
    "committedusediscount", "awssupport", "amazonsupport",
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
