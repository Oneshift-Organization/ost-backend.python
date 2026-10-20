"""O que os arquivos enviados permitem (PLANO_CENARIOS_DE_ENTRADA §3.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.domain.services.diagnostico_de_entrada import Diagnostico, Problema

Nivel = Literal["critico", "alerta", "informativo"]


class ProblemaDeEntradaOut(BaseModel):
    nivel: Nivel
    codigo: str
    titulo: str
    texto: str

    @classmethod
    def de(cls, p: Problema) -> ProblemaDeEntradaOut:
        return cls.model_validate(
            {"nivel": p.nivel, "codigo": p.codigo, "titulo": p.titulo, "texto": p.texto}
        )


class DiagnosticoDeEntradaOut(BaseModel):
    """`nivel`: o pior entre crítico e alerta (a cor do aviso); null sem nenhum dos dois."""

    nivel: Literal["critico", "alerta"] | None
    problemas: list[ProblemaDeEntradaOut]

    @classmethod
    def de(cls, d: Diagnostico) -> DiagnosticoDeEntradaOut:
        return cls(nivel=d.nivel, problemas=[ProblemaDeEntradaOut.de(p) for p in d.problemas])


class BillingFileCheckResponse(BaseModel):
    """Leitura da amostra do arquivo antes do upload: o formato e o que ele permite.

    `aceita=false`: o upload recusa o arquivo (Cost Explorer, formato não reconhecido).
    """

    aceita: bool
    formato: str | None
    nome_do_formato: str | None
    provedor: str | None
    com_id_de_recurso: bool | None
    problemas: list[ProblemaDeEntradaOut]
    como_exportar: str | None
