"""Alvo sugerido para um nome de serviço que a fatura traz e o DE-PARA não tem.

Fatura como fonte, etapa A (PLANO_CICLO_DE_VIDA_DO_PRECO §10, decisão 2.11 de 17/10):
cada fatura pode trazer um nome novo do mesmo serviço ("Amazon Managed Streaming for
Apache Kafka" para o "Amazon MSK" do DE-PARA). A plataforma só sugere; quem aprova é a
curadoria da OneShift. O DE-PARA é de todos os clientes, e por isso nenhum cliente
aprova.

A sugestão é a linha do DE-PARA do mesmo provedor com o nome mais parecido: palavras em
comum, e a sigla entre parênteses do DE-PARA ("(MSK)") contando como as iniciais do nome
da fatura. Abaixo do mínimo, não sugere nada: a curadoria escolhe.

Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

# Abaixo disto a semelhança é acaso: a curadoria escolhe sem sugestão
SEMELHANCA_MINIMA = 0.6

# Palavras que todo nome de serviço tem e não distinguem um do outro
_VAZIAS = frozenset(
    {"amazon", "aws", "azure", "google", "gcp", "cloud", "oracle", "oci", "service",
     "services", "for", "the", "and", "of", "microsoft", "infrastructure"}
)
_PALAVRA = re.compile(r"[a-z0-9]+")
_SIGLA = re.compile(r"\(([A-Za-z0-9]{2,})\)\s*$")


@dataclass(frozen=True, slots=True)
class Sugestao:
    alvo: str
    semelhanca: float


def _palavras(nome: str) -> list[str]:
    return [p for p in _PALAVRA.findall(nome.lower()) if p not in _VAZIAS]


# Nas iniciais, "Service" conta: "Key Management Service" é KMS
_FORA_DAS_INICIAIS = frozenset(
    {"amazon", "aws", "azure", "google", "oracle", "microsoft", "for", "the", "and", "of"}
)


def _iniciais(nome: str) -> str:
    return "".join(p[0] for p in _PALAVRA.findall(nome.lower()) if p not in _FORA_DAS_INICIAIS)


def semelhanca(nome_da_fatura: str, nome_do_de_para: str) -> float:
    """0 a 1: quanto o nome da fatura parece a linha do DE-PARA."""
    a, b = _palavras(nome_da_fatura), _palavras(nome_do_de_para)
    if not a or not b:
        return 0.0
    sigla = _SIGLA.search(nome_do_de_para)
    if sigla and sigla.group(1).lower() == _iniciais(nome_da_fatura):
        return 1.0
    comuns = len(set(a) & set(b)) / len(set(a) | set(b))
    texto = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return round(max(comuns, texto), 4)


def sugerir(nome_da_fatura: str, linhas_do_de_para: Iterable[str]) -> Sugestao | None:
    """A linha do DE-PARA mais parecida, ou None abaixo do mínimo."""
    melhor: Sugestao | None = None
    for alvo in sorted(set(linhas_do_de_para)):
        nota = semelhanca(nome_da_fatura, alvo)
        if nota >= SEMELHANCA_MINIMA and (melhor is None or nota > melhor.semelhanca):
            melhor = Sugestao(alvo, nota)
    return melhor
