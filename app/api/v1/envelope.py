"""Envelope de erro da superfície de agente: `{codigo, mensagem, retryable}`.

Um modelo não decide entre insistir e desistir a partir de texto livre; o
`retryable` é o que torna a decisão possível.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.use_cases.analysis.payload_do_agente import Categoria, PayloadDoAgenteError

_STATUS_POR_CATEGORIA = {
    Categoria.NAO_ENCONTRADO: status.HTTP_404_NOT_FOUND,
    Categoria.CONFLITO: status.HTTP_409_CONFLICT,
    Categoria.INVALIDO: status.HTTP_422_UNPROCESSABLE_ENTITY,
    Categoria.EXPIRADO: status.HTTP_410_GONE,
}


def erro(
    status_code: int, codigo: str, mensagem: str, *, retryable: bool, **extra: Any
) -> HTTPException:
    corpo: dict[str, Any] = {"codigo": codigo, "mensagem": mensagem, "retryable": retryable}
    corpo.update(extra)
    return HTTPException(status_code=status_code, detail=corpo)


def erro_do_payload(e: PayloadDoAgenteError) -> HTTPException:
    return erro(
        _STATUS_POR_CATEGORIA[e.categoria],
        e.codigo,
        e.mensagem,
        retryable=e.retryable,
        **e.extra,
    )
