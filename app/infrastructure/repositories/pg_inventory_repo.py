"""Repositório do inventário no Postgres (I5).

Grava o conteúdo em lotes (o asyncpg aceita até 32.767 parâmetros por comando:
com ~15 colunas, 1.000 linhas por lote ficam abaixo) e de forma idempotente —
apaga o conteúdo anterior do mesmo inventário antes de inserir, como o billing.

As regras de rede vão serializadas na linha do grupo de segurança e voltam como
``RegraDeRede`` na leitura; o domínio nunca vê o JSON.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.billing_report import JobStatus
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.entities.topologia import (
    Acao,
    Direcao,
    EstadoDoRecurso,
    Inventario,
    Recurso,
    RegraDeRede,
    Relacao,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.infrastructure.database.models.inventory import (
    InventoryModel,
    InventoryRelationModel,
    InventoryResourceModel,
)

_LOTE = 1000


class PgInventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── o envio ─────────────────────────────────────────────────────────

    async def salvar(self, inventario: InventarioEnviado) -> None:
        self._s.add(_para_modelo(inventario))
        await self._s.flush()

    async def atualizar(self, inventario: InventarioEnviado) -> None:
        modelo = await self._modelo(inventario.id, inventario.user_id)
        if modelo is None:
            raise ValueError(f"inventário {inventario.id} não existe")
        novo = _para_modelo(inventario)
        for coluna in (
            "status",
            "provider",
            "source_files",
            "detected_formats",
            "captured_at",
            "captured_at_estimated",
            "resource_count",
            "relation_count",
            "rule_count",
            "counts",
            "fingerprint",
            "declarations",
            "declarations_updated_at",
            "error_message",
        ):
            setattr(modelo, coluna, getattr(novo, coluna))
        await self._s.flush()

    async def obter(self, inventario_id: UUID, user_id: UUID) -> InventarioEnviado | None:
        modelo = await self._modelo(inventario_id, user_id)
        return _para_entidade(modelo) if modelo else None

    async def listar(
        self, user_id: UUID, *, limite: int = 50, deslocamento: int = 0
    ) -> list[InventarioEnviado]:
        stmt = (
            select(InventoryModel)
            .where(InventoryModel.user_id == user_id)
            .order_by(InventoryModel.created_at.desc(), InventoryModel.id)
            .limit(limite)
            .offset(deslocamento)
        )
        return [_para_entidade(m) for m in (await self._s.execute(stmt)).scalars()]

    async def apagar(self, inventario_id: UUID, user_id: UUID) -> bool:
        resultado = await self._s.execute(
            delete(InventoryModel).where(
                InventoryModel.id == inventario_id, InventoryModel.user_id == user_id
            )
        )
        return bool(resultado.rowcount)  # type: ignore[attr-defined]

    async def _modelo(self, inventario_id: UUID, user_id: UUID) -> InventoryModel | None:
        stmt = select(InventoryModel).where(
            InventoryModel.id == inventario_id, InventoryModel.user_id == user_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    # ── o conteúdo ──────────────────────────────────────────────────────

    async def gravar_conteudo(
        self, inventario_id: UUID, user_id: UUID, conteudo: Inventario
    ) -> None:
        for modelo in (InventoryRelationModel, InventoryResourceModel):
            await self._s.execute(
                delete(modelo).where(
                    modelo.inventory_id == inventario_id, modelo.user_id == user_id
                )
            )
        regras_por_grupo: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in conteudo.regras:
            regras_por_grupo[r.grupo].append(_regra_para_json(r))
        linhas = [
            {
                "inventory_id": inventario_id,
                "user_id": user_id,
                "resource_key": r.chave,
                "kind": r.tipo.value,
                "native_type": r.tipo_nativo[:200],
                "name": r.nome,
                "account": r.conta,
                "region": (r.regiao or None) and r.regiao[:100],
                "zone": (r.zona or None) and r.zona[:100],
                "state": r.estado.value,
                "attributes": dict(r.atributos) or None,
                "tags": dict(r.tags) or None,
                "aliases": sorted(r.apelidos),
                "captured_at": r.capturado_em,
                "rules": regras_por_grupo.get(r.chave),
            }
            for r in conteudo.recursos
        ]
        await self._inserir(InventoryResourceModel, linhas)
        await self._inserir(
            InventoryRelationModel,
            [
                {
                    "inventory_id": inventario_id,
                    "user_id": user_id,
                    "source_key": rel.de,
                    "target_key": rel.para,
                    "kind": rel.tipo.value,
                }
                for rel in conteudo.relacoes
            ],
        )

    async def _inserir(self, modelo: Any, linhas: Sequence[dict[str, Any]]) -> None:
        for i in range(0, len(linhas), _LOTE):
            await self._s.execute(insert(modelo), list(linhas[i : i + _LOTE]))

    async def carregar_conteudo(self, inventario: InventarioEnviado) -> Inventario:
        filtro_rec = (
            InventoryResourceModel.inventory_id == inventario.id,
            InventoryResourceModel.user_id == inventario.user_id,
        )
        modelos = (
            (
                await self._s.execute(
                    select(InventoryResourceModel)
                    .where(*filtro_rec)
                    .order_by(InventoryResourceModel.resource_key)
                )
            )
            .scalars()
            .all()
        )
        relacoes = (
            (
                await self._s.execute(
                    select(InventoryRelationModel).where(
                        InventoryRelationModel.inventory_id == inventario.id,
                        InventoryRelationModel.user_id == inventario.user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        regras = [
            _regra_de_json(m.resource_key, bruta) for m in modelos for bruta in (m.rules or [])
        ]
        return Inventario(
            provedor=inventario.provedor or "",
            recursos=tuple(_recurso(m) for m in modelos),
            relacoes=tuple(
                sorted(
                    (
                        Relacao(de=r.source_key, para=r.target_key, tipo=TipoDeRelacao(r.kind))
                        for r in relacoes
                    ),
                    key=lambda x: (x.de, x.para, x.tipo.value),
                )
            ),
            regras=tuple(regras),
            capturado_em=inventario.capturado_em,
            contagens=dict(inventario.contagens),
        )

    async def listar_recursos(
        self,
        inventario_id: UUID,
        user_id: UUID,
        *,
        tipo: TipoDeRecurso | None = None,
        limite: int = 100,
        deslocamento: int = 0,
    ) -> tuple[list[Recurso], int]:
        filtros = [
            InventoryResourceModel.inventory_id == inventario_id,
            InventoryResourceModel.user_id == user_id,
        ]
        if tipo is not None:
            filtros.append(InventoryResourceModel.kind == tipo.value)
        total = (
            await self._s.execute(
                select(func.count()).select_from(InventoryResourceModel).where(*filtros)
            )
        ).scalar_one()
        modelos = (
            await self._s.execute(
                select(InventoryResourceModel)
                .where(*filtros)
                .order_by(InventoryResourceModel.resource_key)
                .limit(limite)
                .offset(deslocamento)
            )
        ).scalars()
        return [_recurso(m) for m in modelos], int(total)

    async def contar_por_tipo(self, inventario_id: UUID, user_id: UUID) -> dict[str, int]:
        linhas = await self._s.execute(
            select(InventoryResourceModel.kind, func.count())
            .where(
                InventoryResourceModel.inventory_id == inventario_id,
                InventoryResourceModel.user_id == user_id,
            )
            .group_by(InventoryResourceModel.kind)
        )
        return {k: int(n) for k, n in sorted(linhas.tuples())}

    async def chaves_existentes(
        self, inventario_id: UUID, user_id: UUID, chaves: Iterable[str]
    ) -> set[str]:
        procuradas = list(set(chaves))
        encontradas: set[str] = set()
        for i in range(0, len(procuradas), _LOTE):
            lote = procuradas[i : i + _LOTE]
            resultado = await self._s.execute(
                select(InventoryResourceModel.resource_key).where(
                    InventoryResourceModel.inventory_id == inventario_id,
                    InventoryResourceModel.user_id == user_id,
                    InventoryResourceModel.resource_key.in_(lote),
                )
            )
            encontradas |= set(resultado.scalars())
        return encontradas


# ── mapeamento ──────────────────────────────────────────────────────────


def _para_modelo(i: InventarioEnviado) -> InventoryModel:
    return InventoryModel(
        id=i.id,
        user_id=i.user_id,
        status=i.status.value,
        provider=i.provedor,
        source_files=list(i.arquivos),
        detected_formats=dict(i.formatos),
        captured_at=i.capturado_em,
        captured_at_estimated=i.capturado_em_estimado,
        resource_count=i.recursos,
        relation_count=i.relacoes,
        rule_count=i.regras,
        counts=dict(i.contagens),
        fingerprint=i.fingerprint,
        declarations=dict(i.declaracoes),
        declarations_updated_at=i.declaracoes_atualizadas_em,
        error_message=i.erro,
        created_at=i.created_at,
        updated_at=i.updated_at,
    )


def _para_entidade(m: InventoryModel) -> InventarioEnviado:
    return InventarioEnviado(
        id=m.id,
        user_id=m.user_id,
        arquivos=list(m.source_files or []),
        status=JobStatus(m.status),
        provedor=m.provider,
        formatos=dict(m.detected_formats or {}),
        capturado_em=m.captured_at,
        capturado_em_estimado=m.captured_at_estimated,
        recursos=m.resource_count,
        relacoes=m.relation_count,
        regras=m.rule_count,
        contagens=dict(m.counts or {}),
        fingerprint=m.fingerprint,
        declaracoes=dict(m.declarations or {}),
        declaracoes_atualizadas_em=m.declarations_updated_at,
        erro=m.error_message,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _recurso(m: InventoryResourceModel) -> Recurso:
    return Recurso(
        chave=m.resource_key,
        tipo=TipoDeRecurso(m.kind),
        tipo_nativo=m.native_type,
        nome=m.name,
        conta=m.account,
        regiao=m.region,
        zona=m.zone,
        estado=EstadoDoRecurso(m.state),
        atributos=dict(m.attributes or {}),
        tags=dict(m.tags or {}),
        apelidos=frozenset(m.aliases or []),
        capturado_em=m.captured_at,
    )


def _seletor_para_json(s: Seletor | None) -> dict[str, Any] | None:
    return None if s is None else {"tipo": s.tipo.value, "valor": s.valor}


def _seletor_de_json(d: dict[str, Any] | None) -> Seletor | None:
    return None if not d else Seletor(TipoDeSeletor(d["tipo"]), d.get("valor"))


def _regra_para_json(r: RegraDeRede) -> dict[str, Any]:
    return {
        "direcao": r.direcao.value,
        "acao": r.acao.value,
        "protocolo": r.protocolo,
        "porta_inicio": r.porta_inicio,
        "porta_fim": r.porta_fim,
        "origem": _seletor_para_json(r.origem),
        "destino": _seletor_para_json(r.destino),
        "prioridade": r.prioridade,
        "descricao": r.descricao,
    }


def _regra_de_json(grupo: str, d: dict[str, Any]) -> RegraDeRede:
    origem = _seletor_de_json(d.get("origem"))
    return RegraDeRede(
        grupo=grupo,
        direcao=Direcao(d["direcao"]),
        acao=Acao(d["acao"]),
        protocolo=d["protocolo"],
        origem=origem if origem is not None else Seletor(TipoDeSeletor.QUALQUER),
        porta_inicio=d.get("porta_inicio"),
        porta_fim=d.get("porta_fim"),
        destino=_seletor_de_json(d.get("destino")),
        prioridade=d.get("prioridade"),
        descricao=d.get("descricao"),
    )
