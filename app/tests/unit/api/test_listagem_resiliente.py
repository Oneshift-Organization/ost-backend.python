"""`GET /v1/analyses` não pode morrer por causa de um registro.

Produção devolvia 500 nesta rota com token válido. A causa: o schema
`AnalysisJobSummary` declara tipos exatos para campos que vêm de
`ai_structured_output` — JSON livre escrito pelo LLM. Basta um job em que
`recommendation` veio como objeto em vez de string para o Pydantic recusar, e
como a construção acontecia dentro do loop sem proteção, **um registro derrubava
a listagem inteira**: o usuário perdia acesso a todas as suas análises.

Cinco dos seis campos do resumo vêm do modelo. Formato inesperado não é
excepcional aqui — é questão de tempo.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.routes.analysis import _inteiro, _texto
from app.schemas.analysis import AnalysisJobSummary


def _resumo(**kw: object) -> AnalysisJobSummary:
    base: dict[str, object] = {
        "job_id": uuid4(),
        "report_id": uuid4(),
        "status": "COMPLETED",
        "created_at": datetime.now(),
    }
    return AnalysisJobSummary(**{**base, **kw})  # type: ignore[arg-type]


# ── o que quebrava antes ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("migrar para OCI", "migrar para OCI"),
        (None, None),
        ({"texto": "migrar"}, "{'texto': 'migrar'}"),  # LLM devolveu objeto
        (["a", "b"], "['a', 'b']"),  # LLM devolveu lista
        (42, "42"),
    ],
)
def test_texto_aceita_o_que_o_llm_mandar(bruto: object, esperado: str | None) -> None:
    assert _texto(bruto) == esperado
    _resumo(recommendation=_texto(bruto))  # não levanta


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        (720, 720),
        (None, None),
        (7.5, 7),  # float com fração: o Pydantic recusaria
        ("720", 720),
        ("alto", None),  # texto onde se esperava número
        ({"score": 1}, None),
        (True, None),  # bool é int em Python; não é um score
    ],
)
def test_inteiro_degrada_em_vez_de_estourar(bruto: object, esperado: int | None) -> None:
    assert _inteiro(bruto) == esperado
    _resumo(viability_score=_inteiro(bruto))  # não levanta


def test_row_count_nulo_vira_zero() -> None:
    """`row_count: int = 0` NÃO usa o default quando recebe None explícito —
    o Pydantic rejeita. `or 0` na rota é o que evita o 500."""
    with pytest.raises(ValidationError):
        _resumo(row_count=None)
    assert _resumo(row_count=None or 0).row_count == 0


# ── a rede de segurança ─────────────────────────────────────────────────


def test_resumo_minimo_sempre_e_construivel() -> None:
    """O fallback do `except ValidationError` precisa ser à prova de tudo: se ELE
    falhar, o 500 volta — e volta justamente no caminho de erro."""
    r = _resumo(error="resumo indisponível: dados da análise em formato inesperado")
    assert r.viability_score is None
    assert r.error is not None
