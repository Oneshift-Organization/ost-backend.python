"""Formato canônico ``oneshift-inventory-v1``.

Para a OCI — cuja busca de recursos não traz anexos nem regras — e para
qualquer origem sem formato nativo (CMDB, script do cliente, outra nuvem). É o
próprio modelo canônico em JSON, então a normalização é validação: tipo,
relação e seletor fora do vocabulário são recusados com a posição do erro, em
vez de entrarem como texto livre que a topologia não entenderia.

As regras de um grupo vão em ``atributos.regras`` do próprio grupo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.entities.topologia import (
    Acao,
    Direcao,
    EstadoDoRecurso,
    Inventario,
    Recurso,
    RegraDeRede,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.infrastructure.inventory_ingestion.construtor import Construtor, Referencia
from app.infrastructure.inventory_ingestion.leitura import ArquivoLido, InventarioInvalidoError
from app.infrastructure.inventory_ingestion.normalizadores.comum import (
    apelidos,
    normalizar_cidr,
    protocolo_canonico,
    sem_vazios,
)

FORMATO = "oneshift-inventory-v1"
_PROVEDORES = Literal["aws", "gcp", "azure", "oci"]


class _Seletor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoDeSeletor
    valor: str | None = Field(default=None, max_length=1024)


class _Regra(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direcao: Direcao = Direcao.ENTRADA
    acao: Acao = Acao.PERMITIR
    protocolo: str = Field(default="todos", max_length=16)
    portas: tuple[int, int] | None = None
    origem: _Seletor
    destino: _Seletor | None = None
    prioridade: int | None = None
    descricao: str | None = Field(default=None, max_length=300)

    @field_validator("portas")
    @classmethod
    def _faixa(cls, v: tuple[int, int] | None) -> tuple[int, int] | None:
        if v is not None and not (0 <= v[0] <= v[1] <= 65535):  # noqa: PLR2004
            raise ValueError("portas precisa ser [inicio, fim] entre 0 e 65535")
        return v


class _Recurso(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=1024)
    tipo: TipoDeRecurso
    tipo_nativo: str | None = Field(default=None, max_length=200)
    nome: str | None = Field(default=None, max_length=500)
    conta: str | None = Field(default=None, max_length=500)
    regiao: str | None = Field(default=None, max_length=100)
    zona: str | None = Field(default=None, max_length=100)
    estado: EstadoDoRecurso = EstadoDoRecurso.DESCONHECIDO
    atributos: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    apelidos: list[str] = Field(default_factory=list)
    capturado_em: datetime | None = None


class _Relacao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    de: str = Field(min_length=1, max_length=1024)
    para: str = Field(min_length=1, max_length=1024)
    tipo: TipoDeRelacao


class _Documento(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formato: Literal["oneshift-inventory-v1"]
    provedor: _PROVEDORES
    capturado_em: datetime | None = None
    recursos: list[_Recurso]
    relacoes: list[_Relacao] = Field(default_factory=list)


class NormalizadorOneShift:
    formato: ClassVar[str] = "oneshift_canonico"

    def __init__(self) -> None:
        self._c: Construtor | None = None

    @classmethod
    def reconhece(cls, objeto: dict[str, Any] | None, primeiro: dict[str, Any] | None) -> bool:
        return objeto is not None and objeto.get("formato") == FORMATO

    def consumir(self, arquivo: ArquivoLido) -> None:
        try:
            doc = _Documento.model_validate(arquivo.objeto or {})
        except ValidationError as e:
            erro = e.errors()[0]
            posicao = ".".join(str(p) for p in erro["loc"])
            raise InventarioInvalidoError(f"{arquivo.nome}: `{posicao}` {erro['msg']}") from e
        if self._c is None:
            self._c = Construtor(doc.provedor)
        elif self._c.provedor != doc.provedor:
            raise InventarioInvalidoError(
                f"{arquivo.nome}: provedor {doc.provedor}, mas outro arquivo é {self._c.provedor}"
            )
        c = self._c
        c.novo_arquivo()
        for r in doc.recursos:
            regras = (
                r.atributos.pop("regras", None) if r.tipo is TipoDeRecurso.GRUPO_SEGURANCA else None
            )
            c.adicionar(
                Recurso(
                    chave=r.id.strip(),
                    tipo=r.tipo,
                    tipo_nativo=r.tipo_nativo or r.tipo.value,
                    nome=r.nome,
                    conta=r.conta,
                    regiao=r.regiao,
                    zona=r.zona,
                    estado=r.estado,
                    atributos=sem_vazios(r.atributos),
                    tags=r.tags,
                    apelidos=apelidos(r.id.strip(), *r.apelidos),
                    capturado_em=r.capturado_em or doc.capturado_em,
                )
            )
            for i, bruta in enumerate(regras or []):
                self._regra(arquivo.nome, r.id.strip(), i, bruta)
        for rel in doc.relacoes:
            c.relacionar(rel.de.strip(), rel.para.strip(), rel.tipo)

    def _regra(self, nome: str, grupo: str, indice: int, bruta: object) -> None:
        assert self._c is not None
        try:
            regra = _Regra.model_validate(bruta)
        except ValidationError as e:
            erro = e.errors()[0]
            posicao = ".".join(str(p) for p in erro["loc"])
            raise InventarioInvalidoError(
                f"{nome}: regra {indice} do grupo {grupo}: `{posicao}` {erro['msg']}"
            ) from e
        origem, ref_origem = _seletor(regra.origem)
        destino, ref_destino = _seletor(regra.destino) if regra.destino else (None, None)
        inicio, fim = regra.portas if regra.portas else (None, None)
        self._c.regra(
            RegraDeRede(
                grupo=grupo,
                direcao=regra.direcao,
                acao=regra.acao,
                protocolo=protocolo_canonico(regra.protocolo),
                origem=origem,
                destino=destino,
                porta_inicio=inicio,
                porta_fim=fim,
                prioridade=regra.prioridade,
                descricao=regra.descricao,
            ),
            origem=ref_origem,
            destino=ref_destino,
        )

    def _resolver(self, ref: Referencia) -> str | None:
        assert self._c is not None
        return ref.ident if self._c.existe(ref.ident) else None

    def finalizar(self) -> Inventario:
        if self._c is None:  # pragma: no cover - só chamado depois de consumir
            raise InventarioInvalidoError("nenhum documento canônico lido")
        return self._c.finalizar(self._resolver)


def _seletor(s: _Seletor) -> tuple[Seletor, Referencia | None]:
    if s.tipo is TipoDeSeletor.GRUPO:
        valor = (s.valor or "").strip()
        return Seletor(TipoDeSeletor.GRUPO, valor), Referencia("grupo_seguranca", valor)
    if s.tipo is TipoDeSeletor.CIDR:
        cidr = normalizar_cidr(s.valor)
        if cidr is None:
            raise InventarioInvalidoError(f"seletor cidr inválido: {s.valor!r}")
        return Seletor(TipoDeSeletor.CIDR, cidr), None
    return Seletor(s.tipo, s.valor), None
