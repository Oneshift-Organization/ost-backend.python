"""Traduções que valem para qualquer provedor (RI4 do PLANO_INVENTARIO_API)."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from app.domain.entities.topologia import PROTOCOLO_TODOS, Seletor, TipoDeSeletor

# Número do protocolo IP → nome canônico. `-1`, `*`, `all` e `any` são "todos".
_PROTOCOLOS = {
    "-1": PROTOCOLO_TODOS,
    "*": PROTOCOLO_TODOS,
    "all": PROTOCOLO_TODOS,
    "any": PROTOCOLO_TODOS,
    "6": "tcp",
    "tcp": "tcp",
    "17": "udp",
    "udp": "udp",
    "1": "icmp",
    "icmp": "icmp",
    "58": "icmp",
    "icmpv6": "icmp",
    "icmp6": "icmp",
}

# Origens que significam "a internet inteira", em qualquer provedor.
_QUALQUER = frozenset({"0.0.0.0/0", "::/0", "*", "internet", "any"})


def protocolo_canonico(valor: object) -> str:
    texto = str(valor if valor is not None else "").strip().lower()
    return _PROTOCOLOS.get(texto, texto or PROTOCOLO_TODOS)


def faixa_de_portas(texto: object) -> tuple[int | None, int | None]:
    """``"22"`` → (22, 22); ``"8000-8100"`` → (8000, 8100); ``"*"`` → todas."""
    bruto = str(texto if texto is not None else "").strip()
    if not bruto or bruto in {"*", "any", "all"}:
        return None, None
    inicio, _, fim = bruto.partition("-")
    try:
        a = int(inicio)
        b = int(fim) if fim else a
    except ValueError:
        return None, None
    return (min(a, b), max(a, b))


def portas_da_aws(protocolo: str, de: object, ate: object) -> tuple[int | None, int | None]:
    """AWS usa `-1` (ou nada) para "todas"; ICMP guarda tipo/código ali."""
    if protocolo not in {"tcp", "udp"}:
        return None, None
    # `select-resource-config` devolve a porta como decimal (`5432.0`): ler só como
    # inteiro transformava toda regra em "todas as portas".
    a = _porta(de)
    b = _porta(ate)
    if a is None or b is None or a < 0 or b < 0:
        return None, None
    return (min(a, b), max(a, b))


def _porta(valor: object) -> int | None:
    if valor is None:
        return -1
    try:
        numero = float(str(valor).strip())
    except ValueError:
        return None
    return int(numero) if numero.is_integer() else None


def seletor_de_endereco(texto: object) -> Seletor:
    """CIDR, IP solto ou rótulo do provedor → seletor canônico."""
    bruto = str(texto if texto is not None else "").strip()
    if bruto.lower() in _QUALQUER:
        return Seletor(TipoDeSeletor.QUALQUER)
    cidr = normalizar_cidr(bruto)
    if cidr is None:
        return Seletor(TipoDeSeletor.SERVICO, bruto)
    if ipaddress.ip_network(cidr).prefixlen == 0:
        return Seletor(TipoDeSeletor.QUALQUER)
    return Seletor(TipoDeSeletor.CIDR, cidr)


def normalizar_cidr(texto: object) -> str | None:
    """``"10.0.1.5"`` → ``"10.0.1.5/32"``; host bits zerados; inválido → None."""
    bruto = str(texto if texto is not None else "").strip()
    if not bruto:
        return None
    try:
        return str(ipaddress.ip_network(bruto, strict=False))
    except ValueError:
        return None


def ip_valido(texto: object) -> str | None:
    bruto = str(texto if texto is not None else "").strip()
    try:
        return str(ipaddress.ip_address(bruto)) if bruto else None
    except ValueError:
        return None


def instante(texto: object) -> datetime | None:
    """ISO-8601 com ou sem `Z`; sempre com fuso (UTC quando não vem)."""
    if not texto:
        return None
    try:
        valor = datetime.fromisoformat(str(texto).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


def lista(valor: object) -> list[Any]:
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    return [valor]


def dicionario(valor: object) -> dict[str, Any]:
    return valor if isinstance(valor, dict) else {}


def caminho(dado: object, *chaves: str) -> Any:
    """``caminho(d, "a", "b")`` = ``d["a"]["b"]``, ou None em qualquer falta."""
    atual: Any = dado
    for chave in chaves:
        if not isinstance(atual, dict):
            return None
        atual = atual.get(chave)
    return atual


def texto(valor: object) -> str | None:
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


def inteiro(valor: object) -> int | None:
    try:
        return int(str(valor).strip()) if valor is not None and str(valor).strip() else None
    except ValueError:
        try:
            return int(float(str(valor)))
        except ValueError:
            return None


def booleano(valor: object) -> bool | None:
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return None
    t = str(valor).strip().lower()
    if t in {"true", "yes", "1", "enabled"}:
        return True
    if t in {"false", "no", "0", "disabled"}:
        return False
    return None


def tags_de_pares(pares: Iterable[object]) -> dict[str, str]:
    """Lista de ``{key, value}``, ``{Key, Value}`` ou ``{tagName, tagValue}``."""
    tags: dict[str, str] = {}
    for par in pares:
        if not isinstance(par, Mapping):
            continue
        chave = par.get("key", par.get("Key", par.get("tagName")))
        valor = par.get("value", par.get("Value", par.get("tagValue")))
        if chave is not None:
            tags[str(chave)] = "" if valor is None else str(valor)
    return tags


def tags_de(valor: object) -> dict[str, str]:
    """Tags em qualquer das formas: objeto ``{chave: valor}`` ou lista de pares.
    ``select-resource-config`` traz lista de ``{key, value, tag}``; o snapshot, objeto."""
    if isinstance(valor, Mapping):
        return tags_de_objeto(valor)
    if isinstance(valor, list):
        return tags_de_pares(valor)
    return {}


def simples(valor: object) -> object:
    """``{"value": x}`` → ``x``. ``select-resource-config`` embrulha assim alguns campos
    (estado do disco, grupos do balanceador); o snapshot traz o valor direto."""
    if isinstance(valor, Mapping) and set(valor) == {"value"}:
        return valor["value"]
    return valor


def tags_de_objeto(valor: object) -> dict[str, str]:
    if not isinstance(valor, Mapping):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in valor.items()}


def sem_vazios(atributos: Mapping[str, Any]) -> dict[str, Any]:
    """Tira chave com None, lista vazia ou texto vazio: o JSONB fica limpo e a
    ausência continua distinguível de `false`."""
    return {k: v for k, v in atributos.items() if not _vazio(v)}


def _vazio(valor: object) -> bool:
    return valor is None or (isinstance(valor, (str, list, dict)) and not valor)


def apelidos(*valores: object) -> frozenset[str]:
    return frozenset(str(v).strip().lower() for v in valores if v is not None and str(v).strip())
