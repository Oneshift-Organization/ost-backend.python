"""Cotações de câmbio de referência do BCE, via Frankfurter (público, sem auth).

## Por que existe

O câmbio vinha de `fx_brl_usd` no ambiente: um número solto, sem data e sem
ninguém encarregado de atualizá-lo. Em 07/09/2026 esse número era `5.70` e a
cotação real, `5.1261` — **11% de erro** sobre metade das linhas do bill GCP,
que são em BRL. O erro não aparecia em lugar nenhum porque a taxa não tinha data
para comparar.

Uma taxa buscada traz `data` e `fonte` reais, e é isso que faz
`alertas.avaliar_cambio` conseguir dizer "esta cotação tem 120 dias" em vez de
"não sei de quando é".

## Por que esta fonte

Taxas de referência do Banco Central Europeu, publicadas em dia útil por volta
das 16h CET. São referência contábil, não preço de fechamento de mercado — o que
é adequado aqui: a análise compara custo mensal, não executa operação cambial.
Sem chave, sem SDK, resposta de poucos KB.

Mesmo padrão dos clientes de preço: `urllib` da stdlib, roda offline via
`scripts/sync_fx.py`, **nunca** no caminho crítico da análise.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain.services.currency import ANALYSIS_CURRENCY, FxRate

_URL = "https://api.frankfurter.dev/v1/latest?base={base}&symbols={symbols}"

# Vai para a coluna `fonte` e aparece no ledger `premissas.fx.fonte`. Nomear o
# publicador, não o proxy: quem audita precisa saber de quem é o número.
FONTE = "ecb_referencia"

# Moedas que aparecem nos bills que processamos hoje. Buscar todas de uma vez
# custa a mesma requisição.
MOEDAS_PADRAO = ("BRL", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "MXN")


def fetch_fx_rates(
    moedas: tuple[str, ...] = MOEDAS_PADRAO,
    *,
    destino: str = ANALYSIS_CURRENCY,
    timeout: float = 30.0,
) -> list[FxRate]:
    """Cotações ``<moeda>/<destino>`` do último dia útil publicado.

    A API responde "quantas unidades da moeda valem 1 unidade da base", que é
    exatamente a convenção de `FxRate.taxa` quando a base é a moeda de análise.
    Chamar com ``base=USD`` evita ter de inverter a fração aqui — inverter é
    onde esse tipo de código costuma errar, e um câmbio invertido produz
    números plausíveis na direção errada.

    Degrada para lista vazia se a fonte estiver fora: o sync não é caminho
    crítico e derrubá-lo apagaria as taxas que já estão no banco.
    """
    alvos = tuple(m.upper() for m in moedas if m.upper() != destino.upper())
    if not alvos:
        return []

    url = _URL.format(base=destino.upper(), symbols=",".join(alvos))
    # O User-Agent não é cosmético: a fonte responde **403** ao
    # `Python-urllib/3.x` default. Sem ele o fetch falha silenciosamente e o
    # câmbio continua vindo do env — que é exatamente o problema a corrigir.
    req = urllib.request.Request(  # noqa: S310 - host fixo (frankfurter.dev)
        url, headers={"User-Agent": "oneshift-pricing/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosemgrep:python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            bruto = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    cotacao = _parse_data(bruto.get("date"))
    if cotacao is None:
        # Sem data não há por que gravar: o valor da tabela sobre o env é
        # justamente ter data.
        return []

    taxas: list[FxRate] = []
    for moeda, valor in (bruto.get("rates") or {}).items():
        try:
            taxa = Decimal(str(valor))
        except (InvalidOperation, TypeError):
            continue
        if taxa <= 0:
            continue
        taxas.append(
            FxRate(
                par=f"{moeda.upper()}/{destino.upper()}",
                taxa=taxa,
                data=cotacao,
                fonte=FONTE,
            )
        )
    return taxas


def _parse_data(valor: object) -> date | None:
    try:
        return date.fromisoformat(str(valor))
    except (ValueError, TypeError):
        return None
