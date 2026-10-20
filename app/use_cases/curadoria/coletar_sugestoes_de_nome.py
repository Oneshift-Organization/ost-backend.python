"""Coleta os nomes de serviço das faturas que o DE-PARA não reconhece (decisão 2.11).

Um nome entra na fila quando nenhum caminho da análise o reconhece: a linha do DE-PARA
(pela `service_key`), o apelido fixo, o apelido já aprovado, a dica de tipo, a escolha
pelo uso (nome ambíguo como "Storage") e o componente financeiro. A sugestão de alvo é a
linha do DE-PARA mais parecida (`domain/services/sugestao_de_nome`); quem decide é a
curadoria da OneShift.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from app.domain.interfaces.i_service_name_suggestion_repo import (
    IServiceNameSuggestionRepository,
)
from app.domain.interfaces.i_service_repositories import IServiceComparisonRepository
from app.domain.services.service_identity import (
    is_financial_component,
    service_key,
    service_type_hint,
)
from app.domain.services.servico_pelo_uso import REGRAS as NOMES_RESOLVIDOS_PELO_USO
from app.domain.services.sugestao_de_nome import sugerir
from app.use_cases.analysis.build_migration_context import PROVIDER_SERVICE_ALIASES


@dataclass(frozen=True, slots=True)
class ResultadoDaColeta:
    lidos: int
    na_fila: int
    com_sugestao: int


class ColetarSugestoesDeNomeUseCase:
    def __init__(
        self,
        sugestoes: IServiceNameSuggestionRepository,
        de_para: IServiceComparisonRepository,
    ) -> None:
        self._sugestoes = sugestoes
        self._de_para = de_para

    async def execute(self, usuarios_excluidos: list[UUID]) -> ResultadoDaColeta:
        servicos = await self._sugestoes.servicos_das_faturas(usuarios_excluidos)
        linhas: dict[str, list[str]] = defaultdict(list)
        for linha in await self._de_para.list_all():
            linhas[linha.provider.lower()].append(linha.service_name)
        chaves = {p: {service_key(p, n) for n in nomes} for p, nomes in linhas.items()}
        aprovados = {p: await self._de_para.apelidos_aprovados(p) for p in linhas}

        na_fila = com_sugestao = 0
        for servico in servicos:
            provider = servico.provider
            canonico = PROVIDER_SERVICE_ALIASES.get(provider, {}).get(servico.nome, servico.nome)
            chave = service_key(provider, canonico)
            if (
                chave in chaves.get(provider, set())
                or service_key(provider, servico.nome) in aprovados.get(provider, {})
                or service_type_hint(provider, servico.nome) is not None
                or (provider, servico.nome.strip().casefold()) in NOMES_RESOLVIDOS_PELO_USO
                or is_financial_component(servico.nome)
            ):
                continue
            sugestao = sugerir(servico.nome, linhas.get(provider, []))
            await self._sugestoes.registrar(
                servico,
                service_key(provider, servico.nome),
                sugestao.alvo if sugestao else None,
                sugestao.semelhanca if sugestao else None,
            )
            na_fila += 1
            com_sugestao += sugestao is not None
        return ResultadoDaColeta(len(servicos), na_fila, com_sugestao)
