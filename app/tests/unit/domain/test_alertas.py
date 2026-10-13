"""Alertas do backend contra os próprios insumos.

O que estes testes protegem: um câmbio que não existe **não** pode sair como
conversão silenciosa. Antes disso, `SEM_TAXA_CAMBIO` era um rótulo que ninguém
lia — a análise somava euro com dólar e devolvia um número plausível.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.services.alertas import ORIGEM_BACKEND, avaliar_cambio, avaliar_regioes
from app.domain.services.confidence import ConfiancaAnalise, avaliar_confianca
from app.domain.services.currency import FxRate, identity_rate, taxa_desconhecida

HOJE = date(2026, 9, 7)


def _config(taxa: str = "5.70") -> FxRate:
    """O estado real de hoje: número no ambiente, sem data."""
    return FxRate(par="BRL/USD", taxa=Decimal(taxa), fonte="config")


# ── sem taxa: o caso que motivou o módulo ───────────────────────────────


def test_sem_taxa_rebaixa_e_avisa() -> None:
    a = avaliar_cambio(taxa_desconhecida("EUR/USD"), HOJE)
    assert a is not None
    assert a.severidade == "alta"
    assert a.rebaixa, "converter por 1 entre moedas diferentes não é imprecisão"
    assert "EUR/USD" in a.titulo


def test_sem_taxa_vai_para_a_tela_com_origem_backend() -> None:
    """A sugestão tem que ser indistinguível em formato das do agente, e
    distinguível em origem — a tela precisa dizer de quem é a dúvida."""
    s = avaliar_cambio(taxa_desconhecida("EUR/USD"), HOJE).as_sugestao()
    assert s["origem"] == ORIGEM_BACKEND
    assert s["efeito_backend"] == "confianca_rebaixada"
    assert set(s) >= {"tipo", "severidade", "titulo", "detalhe", "referencia"}


# ── moeda já em USD ─────────────────────────────────────────────────────


def test_origem_em_dolar_nao_gera_alerta() -> None:
    """Não houve conversão a questionar. Alertar aqui treinaria quem lê a
    ignorar a lista."""
    assert avaliar_cambio(identity_rate("USD"), HOJE) is None


# ── idade da cotação ────────────────────────────────────────────────────


def test_taxa_do_dia_nao_gera_alerta() -> None:
    fresca = FxRate(par="BRL/USD", taxa=Decimal("5.1261"), data=HOJE, fonte="ecb")
    assert avaliar_cambio(fresca, HOJE) is None


def test_taxa_de_120_dias_rebaixa() -> None:
    velha = FxRate(
        par="BRL/USD", taxa=Decimal("5.70"), data=date(2026, 5, 10), fonte="ecb"
    )
    a = avaliar_cambio(velha, HOJE)
    assert a is not None and a.rebaixa
    assert "120" in a.titulo, "a idade tem que aparecer para ser contestável"


def test_taxa_de_45_dias_avisa_sem_rebaixar() -> None:
    a = avaliar_cambio(
        FxRate(par="BRL/USD", taxa=Decimal("5.2"), data=date(2026, 7, 24), fonte="ecb"),
        HOJE,
    )
    assert a is not None
    assert not a.rebaixa
    assert a.as_sugestao()["efeito_backend"] == "informativo"


def test_taxa_sem_data_avisa_mas_nao_rebaixa() -> None:
    """O fallback de ambiente é o estado normal hoje. Rebaixar toda análise em
    BRL esvaziaria o nível justamente onde ele precisa discriminar — o aviso
    aparece, o número não muda."""
    a = avaliar_cambio(_config(), HOJE)
    assert a is not None
    assert not a.rebaixa
    assert a.tipo == "dado_faltante"


# ── efeito na confiança ─────────────────────────────────────────────────


def test_rebaixamento_carrega_o_motivo() -> None:
    """Um nível `low` sobre razão 0,95 pareceria bug sem o motivo ao lado."""
    conf = avaliar_confianca({"official": Decimal("100")})
    assert conf.nivel == "high"

    a = avaliar_cambio(taxa_desconhecida("EUR/USD"), HOJE)
    rebaixada = conf.com_rebaixamento([a.titulo])

    assert rebaixada.nivel == "medium"
    assert rebaixada.rebaixada_por == (a.titulo,)
    assert rebaixada.razao_confiavel == conf.razao_confiavel, "a razão não muda"
    assert rebaixada.as_ledger()["rebaixada_por"] == [a.titulo]


def test_sem_motivo_a_confianca_e_a_mesma_instancia_logica() -> None:
    conf = avaliar_confianca({"official": Decimal("100")})
    assert conf.com_rebaixamento([]) == conf


def test_dois_motivos_rebaixam_um_degrau_so() -> None:
    """Mesma regra da contestação do agente: dois problemas não tornam a
    análise duas vezes menos confiável."""
    conf = ConfiancaAnalise(
        nivel="high",
        razao_confiavel=1.0,
        custo_confiavel=1.0,
        custo_total=1.0,
        por_rotulo={},
    )
    assert conf.com_rebaixamento(["a", "b"]).nivel == "medium"


# ── Cotação regional (PLANO_PRECO_REGIONAL R4) ───────────────────────────────


def test_regioes_sem_problema_nao_alertam() -> None:
    assert avaliar_regioes(sem_zona=[], zona_padrao="virginia", aproximadas=[]) == []


def test_regiao_desconhecida_e_preco_de_outra_regiao_alertam_sem_rebaixar() -> None:
    alertas = avaliar_regioes(
        sem_zona=["E2/global"],
        zona_padrao="virginia",
        aproximadas=[
            {"provedor": "azure", "pedida": "brazilsouth", "cotada": "eastus", "uso": "compute"}
        ],
    )
    assert [a.titulo for a in alertas] == [
        "Região de origem não identificada",
        "Preço de outra região na cotação",
    ]
    assert "virginia" in alertas[0].detalhe
    assert "azure brazilsouth → eastus" in alertas[1].detalhe
    assert not any(a.rebaixa for a in alertas), "o rótulo reference já rebaixa"
