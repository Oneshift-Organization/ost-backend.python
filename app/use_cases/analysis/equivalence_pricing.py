"""Attach existing scenario prices without duplicating category budgets per service."""

from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal

from app.domain.services.decision_support import number
from app.domain.services.sku_normalization import normalize_provider

_CENT = Decimal("0.01")
_CATEGORIES = {
    "armazenamento_ssd": ("Discos SSD", {"block_storage"}),
    "armazenamento_hdd": ("Discos HDD", {"block_storage"}),
    "armazenamento_objetos": ("Armazenamento de objetos", {"object_storage"}),
    "egresso_rede": ("Tráfego de saída", {"network_egress"}),
    # Rede por tipo (PLANO_PRECO_NAO_COMPUTE). Sem `por_servico` no payload, o serviço
    # inteiro só casa com uma categoria e a rede aparece como linha agregada.
    "transferencia_entre_zonas": ("Tráfego entre zonas", set()),
    "balanceador": ("Balanceador de carga", {"load_balancer"}),
    "balanceador_dados": ("Processamento do balanceador", {"load_balancer"}),
    "nat": ("NAT", {"nat_gateway"}),
    "nat_dados": ("Dados processados pelo NAT", {"nat_gateway"}),
    "ip_publico": ("IP público", set()),
    "vpn": ("VPN", {"vpn_gateway"}),
    "banco_dados": ("Bancos de dados", {"relational_database", "managed_relational_database"}),
    # R4: plano de controle, segredos, chaves e CDN, pela quantidade medida
    "gke_plano_controle": ("Plano de controle do Kubernetes", {"managed_kubernetes"}),
    "segredos": ("Segredos", {"secrets_management"}),
    "segredos_operacoes": ("Operações de segredos", {"secrets_management"}),
    "chaves": ("Chaves de criptografia", {"key_management"}),
    "chaves_operacoes": ("Operações de chaves", {"key_management"}),
    "cdn": ("CDN", {"content_delivery_network"}),
    "cdn_requisicoes": ("Requisições da CDN", {"content_delivery_network"}),
}


def _provider(target: dict) -> str:
    return normalize_provider(str(target.get("provider") or target.get("provedor") or "")).lower()


def _eligible(sources: list[dict], kinds: set[str], provider: str) -> list[dict]:
    return [
        s
        for s in sources
        if (
            s.get("service_type") in kinds
            and s.get("type_source") == "curado"
            and (number(s.get("cost")) or Decimal(0)) > 0
            and any(_provider(t) == provider for t in s.get("targets", []))
            and not any(
                _provider(t) == provider and t.get("financials") for t in s.get("targets", [])
            )
        )
    ]


_PREMISSA = {
    "category_rate_ratio": "Razão de tarifas de referência; não é cotação de SKU nem uso medido.",
    "quantidade_medida": "Quantidade medida na fatura vezes a tarifa do destino; sem descontos.",
}
_ORDEM_DA_CONFIANCA = {"official": 3, "reference": 2, "low": 1}


def _metodo(quote: dict) -> str:
    metodo = quote.get("metodo")
    return metodo if metodo in _PREMISSA else "category_rate_ratio"


def _financials(quote: dict, base: Decimal, price: Decimal) -> dict:
    return {
        "custo_mensal_estimado": float(price),
        "delta_custo": float(price - base),
        "confianca_preco": quote.get("confianca_preco", "low"),
        "pricing_method": _metodo(quote),
        "pricing_evidence": {
            **quote,
            "source_cost": float(base),
            "assumption": _PREMISSA[_metodo(quote)],
        },
    }


def _linha_agregada(
    key: str, label: str, provider: str, *, base: Decimal, price: Decimal, quote: dict
) -> dict:
    return {
        "identity": f"category/{key}/{provider}",
        "name": f"{label} (agregado)",
        "source_service": label,
        "cost": float(base),
        "basis": "category_budget",
        "targets": [
            {
                "provider": provider,
                "service_name": f"{label} — cenário {provider.upper()}",
                "financials": _financials(quote, base, price),
            }
        ],
        "scoped_provider": provider,
    }


def _allocate(members: list[dict], provider: str, base: Decimal, quote: dict) -> None:
    """Stable cent allocation; alternatives share a budget and are never additive."""
    total = number(quote["custo_mensal_estimado"])
    remainder = total
    for index, member in enumerate(members):
        source_cost = number(member["cost"])
        price = (total * source_cost / base).quantize(_CENT, rounding=ROUND_HALF_UP)
        if index == len(members) - 1:
            price = remainder
        remainder -= price
        for target in member["targets"]:
            if _provider(target) == provider:
                target["financials"] = _financials(quote, source_cost, price)


def attach_category_prices(sources: list[dict], payload: dict) -> list[dict]:
    """Allocate only when the *whole* source category reconciles; otherwise expose its scope."""
    sources = deepcopy(sources)
    auxiliary = payload.get("custos_auxiliares") or {}
    por_servico = auxiliary.get("por_servico")
    if isinstance(por_servico, dict) and por_servico:
        return _pela_parte_do_servico(sources, auxiliary, por_servico)
    for key, (label, kinds) in _CATEGORIES.items():
        base = number(auxiliary.get(key))
        if base is None or base <= 0:
            continue
        for quote in auxiliary.get(f"equivalentes_{key}", []):
            price = number(quote.get("custo_mensal_estimado"))
            if price is None or price < 0:
                continue
            provider = _provider(quote)
            members = sorted(_eligible(sources, kinds, provider), key=lambda s: s["identity"])
            if key in {"armazenamento_ssd", "armazenamento_hdd"} and all(
                (number(auxiliary.get(k)) or Decimal(0)) > 0
                for k in ("armazenamento_ssd", "armazenamento_hdd")
            ):
                members = []  # mixed storage media need dimension-specific consumption
            member_total = sum((number(m["cost"]) for m in members), Decimal(0))
            # Exact cents only; a service can contain several billing dimensions.
            if members and member_total == base:
                _allocate(members, provider, base, quote)
                continue
            sources.append(
                _linha_agregada(key, label, provider, base=base, price=price, quote=quote)
            )
    return sources


def _cotacoes(auxiliary: dict) -> dict[tuple[str, str], tuple[str, Decimal, Decimal, dict]]:
    """(chave, destino) → (rótulo, custo da categoria, preço no destino, cotação)."""
    cotacoes: dict[tuple[str, str], tuple[str, Decimal, Decimal, dict]] = {}
    for key, (label, _kinds) in _CATEGORIES.items():
        base = number(auxiliary.get(key))
        if base is None or base <= 0:
            continue
        for quote in auxiliary.get(f"equivalentes_{key}", []):
            price = number(quote.get("custo_mensal_estimado"))
            if price is not None and price >= 0:
                cotacoes[(key, _provider(quote))] = (label, base, price, quote)
    return cotacoes


# Serviço identificado pelo catálogo: curado ou pelo apelido/uso (não inferido)
_IDENTIFICADO = frozenset({"curado", "catalog_alias"})


def _partes(source: dict, por_servico: dict) -> dict[str, Decimal]:
    """Custo do serviço em cada categoria auxiliar (cotada ou não)."""
    custo = number(source.get("cost"))
    if source.get("type_source") not in _IDENTIFICADO or custo is None or custo <= 0:
        return {}
    partes = {
        key: valor
        for key, por_nome in por_servico.items()
        if isinstance(por_nome, dict)
        and (valor := number(por_nome.get(source.get("name")))) is not None
        and valor > 0
    }
    # Cada valor do payload é arredondado sozinho: até um centavo por parte
    if sum(partes.values(), Decimal(0)) > custo + _CENT * len(partes):
        return {}
    return partes


def _financials_por_parte(
    partes: dict[str, Decimal],
    precos: dict[str, Decimal],
    cotacoes: dict[tuple[str, str], tuple[str, Decimal, Decimal, dict]],
    provider: str,
    custo: Decimal,
) -> dict:
    # O que não tem tarifa no destino (rede sem cotação, categoria que ele não cota,
    # linha fora das categorias) fica ao custo atual, como no total do cenário
    mantido = max(custo - sum(partes.values(), Decimal(0)), Decimal(0))
    total = sum(precos.values(), Decimal(0)) + mantido
    quotes = {key: cotacoes[(key, provider)][3] for key in partes}
    metodos = {_metodo(q) for q in quotes.values()}
    metodo = "quantidade_medida" if metodos == {"quantidade_medida"} else "category_rate_ratio"
    return {
        "custo_mensal_estimado": float(total),
        "delta_custo": float(total - custo),
        "confianca_preco": min(
            (str(q.get("confianca_preco", "low")) for q in quotes.values()),
            key=lambda c: _ORDEM_DA_CONFIANCA.get(c, 0),
        ),
        "pricing_method": metodo,
        "pricing_evidence": {
            "partes": [
                {
                    "categoria": cotacoes[(key, provider)][0],
                    "custo_origem": float(partes[key]),
                    "custo_destino": float(precos[key]),
                    "metodo": _metodo(quotes[key]),
                    "taxa_destino": quotes[key].get("taxa_destino"),
                    "unidade": quotes[key].get("unidade"),
                }
                for key in sorted(partes)
            ]
            + (
                [
                    {
                        "categoria": "Mantido ao custo atual",
                        "custo_origem": float(mantido),
                        "custo_destino": float(mantido),
                        "metodo": "sem_tarifa_no_destino",
                    }
                ]
                if mantido > 0
                else []
            ),
            "source_cost": float(custo),
            "assumption": "Preço de cada categoria pela parte do serviço nela. "
            + " ".join(_PREMISSA[m] for m in sorted(metodos)),
        },
    }


def _pela_parte_do_servico(sources: list[dict], auxiliary: dict, por_servico: dict) -> list[dict]:
    """Cada serviço recebe o preço das categorias pela parte dele (PLANO_PRECO_NAO_COMPUTE R2).

    O balanceador da AWS espalha linhas por hora, processamento, egress e tráfego entre
    zonas; pela regra antiga (o serviço inteiro igual a uma categoria) ficava sem preço.
    Ganha preço o serviço identificado com ao menos uma parte cotada no destino; a parte
    sem tarifa lá fica ao custo atual, como no total do cenário. O resto de cada
    categoria vira a linha agregada, e a conciliação fecha como antes.
    """
    cotacoes = _cotacoes(auxiliary)
    alocado: dict[tuple[str, str], list[Decimal]] = {}
    for source in sorted(sources, key=lambda s: s["identity"]):
        partes = _partes(source, por_servico)
        if not partes:
            continue
        custo = number(source["cost"]) or Decimal(0)
        por_provedor: dict[str, list[dict]] = {}
        for target in source.get("targets", []):
            por_provedor.setdefault(_provider(target), []).append(target)
        for provider, targets in sorted(por_provedor.items()):
            if any(t.get("financials") for t in targets):
                continue
            cotadas = {key: parte for key, parte in partes.items() if (key, provider) in cotacoes}
            if not cotadas:
                continue
            precos = {
                key: (cotacoes[(key, provider)][2] * parte / cotacoes[(key, provider)][1]).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
                for key, parte in cotadas.items()
            }
            for key, parte in cotadas.items():
                acumulado = alocado.setdefault((key, provider), [Decimal(0), Decimal(0)])
                acumulado[0] += parte
                acumulado[1] += precos[key]
            financials = _financials_por_parte(cotadas, precos, cotacoes, provider, custo)
            # Alternativas dividem o orçamento e nunca somam
            for target in targets:
                target["financials"] = financials
    for (key, provider), (label, base, price, quote) in sorted(cotacoes.items()):
        base_alocada, preco_alocado = alocado.get((key, provider), [Decimal(0), Decimal(0)])
        if base - base_alocada > _CENT:
            sources.append(
                _linha_agregada(
                    key,
                    label,
                    provider,
                    base=base - base_alocada,
                    price=price - preco_alocado,
                    quote=quote,
                )
            )
    return sources


def reconcile_pricing(rows: list, payload: dict) -> list[dict]:
    """Explain graph = selected scoped prices + unchanged source balance, at cents precision."""
    summary = payload.get("resumo_executivo") or {}
    current = number(summary.get("custo_mensal_atual"))
    if current is None:
        return []
    # Compromisso que paga máquina virtual e saiu da origem junto com os pools
    # (premissas.compromisso_de_compute). Não é linha da tabela: sem ele aqui, o
    # total não fecharia. Payload sem a premissa reconcilia como antes.
    compromisso = ((payload.get("premissas") or {}).get("compromisso_de_compute") or {}).get(
        "por_provedor"
    ) or {}
    result = []
    for scenario in summary.get("por_provedor", []):
        provider = _provider(scenario)
        selected = [
            r for r in rows if r.target_provider.lower() == provider and r.included_in_estimate
        ]
        covered = sum((number(r.source_monthly_cost) or Decimal(0) for r in selected), Decimal(0))
        covered += number(compromisso.get(provider)) or Decimal(0)
        priced = sum((number(r.estimated_monthly_cost) or Decimal(0) for r in selected), Decimal(0))
        expected = number(scenario.get("custo_estimado_total"))
        residual = current - covered
        total = residual + priced
        # Pool components and totals are independently rounded on serialization.
        tolerance = _CENT * max(1, len(selected))
        reconciled = expected is not None and residual >= 0 and abs(total - expected) <= tolerance
        result.append(
            {
                "provider": provider,
                "currency": payload.get("moeda", "USD"),
                "status": "reconciled" if reconciled else "review_required",
                "source_covered": float(covered),
                "priced_target": float(priced),
                "unchanged_source_balance": float(residual),
                "estimated_total": float(total),
                "published_total": float(expected) if expected is not None else None,
                "priced_components": len(selected),
                "note": (
                    "Saldo sem cotação mantido ao custo atual; não é preço validado no destino."
                    if reconciled
                    else "Escopos não reconciliados; revisar antes de usar a economia."
                ),
            }
        )
    return result
