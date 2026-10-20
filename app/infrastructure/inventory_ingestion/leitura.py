"""Leitura segura dos arquivos de inventário (RNF4 do PLANO_INVENTARIO_API).

O arquivo vem do cliente: pode ser um snapshot do AWS Config de 80 MB
compactado, um NDJSON do Asset Inventory com meio milhão de linhas ou um zip
com as páginas do Resource Graph. Três cuidados:

- **Compressão pelo conteúdo, não pela extensão.** Gzip e zip são reconhecidos
  pelos bytes mágicos; um `.json` que na verdade é gzip funciona.
- **Orçamento de bytes descompactados.** Cada leitura passa por um contador
  compartilhado entre todos os arquivos do envio. Um zip-bomb para no limite,
  com mensagem, em vez de derrubar o processo.
- **NDJSON em streaming.** Documento JSON único é lido inteiro (tem teto
  próprio, menor); NDJSON é lido linha a linha e nunca está inteiro na memória.

Cada arquivo vira um ``ArquivoLido``: ou um objeto JSON (o envelope, como o
snapshot do Config), ou uma sequência de itens (array JSON ou NDJSON). Quem
decide o que é cada coisa é o normalizador.
"""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import IO, Any, cast

from app.core.exceptions import IngestionError

_GZIP_MAGIC = b"\x1f\x8b"
_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_VAZIO = b"PK\x05\x06"
_BOM = "﻿"
_BLOCO = 1 << 16
_MB = 1024 * 1024


class InventarioInvalidoError(IngestionError):
    """Arquivo que não dá para ler como inventário. A mensagem vai para o cliente."""


class LimiteExcedidoError(InventarioInvalidoError):
    """Passou de um limite da RNF4."""


@dataclass(frozen=True, slots=True)
class Limites:
    # Documento JSON único é lido inteiro: `json.loads` de 100 MB já custa
    # perto de 1 GB de memória. NDJSON não tem esse teto, só o total.
    bytes_documento_unico: int = 100 * _MB
    bytes_total: int = 1024 * _MB
    arquivos_no_zip: int = 200
    recursos: int = 200_000


@dataclass(frozen=True, slots=True)
class ArquivoLido:
    """Um arquivo (ou membro de zip) já decodificado.

    ``objeto`` quando o conteúdo é um objeto JSON; ``itens`` quando é uma
    sequência (array JSON ou NDJSON). Exatamente um dos dois é preenchido.
    ``itens`` pode ser um gerador: consome-se uma vez só.
    """

    nome: str
    objeto: dict[str, Any] | None = None
    itens: Iterator[dict[str, Any]] | None = None


class _Orcamento:
    """Bytes descompactados ainda disponíveis para o envio inteiro."""

    def __init__(self, limite: int) -> None:
        self.limite = limite
        self.usado = 0

    def gastar(self, n: int, nome: str) -> None:
        self.usado += n
        if self.usado > self.limite:
            raise LimiteExcedidoError(
                f"{nome}: o envio passou de {self.limite // _MB} MB descompactados"
            )


class _LeitorContado(io.RawIOBase):
    """Envolve um stream binário e desconta cada byte lido do orçamento."""

    def __init__(self, fonte: IO[bytes], orcamento: _Orcamento, nome: str) -> None:
        self._fonte = fonte
        self._orcamento = orcamento
        self._nome = nome

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        dados = self._fonte.read(len(buffer))
        n = len(dados)
        buffer[:n] = dados
        self._orcamento.gastar(n, self._nome)
        return n

    def close(self) -> None:
        self._fonte.close()
        super().close()


def ler_arquivos(
    arquivos: list[tuple[str, Path]], limites: Limites | None = None
) -> Iterator[ArquivoLido]:
    """Um ``ArquivoLido`` por arquivo ou membro de zip, na ordem recebida.

    Os arquivos precisam continuar no disco enquanto o iterador é consumido:
    os itens de NDJSON são lidos sob demanda.
    """
    lim = limites or Limites()
    orcamento = _Orcamento(lim.bytes_total)
    for nome, caminho in arquivos:
        with caminho.open("rb") as bruto:
            cabeca = bruto.read(4)
        if cabeca.startswith((_ZIP_MAGIC, _ZIP_VAZIO)):
            yield from _membros_do_zip(nome, caminho, lim, orcamento)
        else:
            yield _um_arquivo(nome, _abridor_de_arquivo(caminho), lim, orcamento)


def _abridor_de_arquivo(caminho: Path) -> Callable[[], IO[bytes]]:
    return lambda: caminho.open("rb")


def _abridor_de_membro(pacote: zipfile.ZipFile, membro: zipfile.ZipInfo) -> Callable[[], IO[bytes]]:
    return lambda: pacote.open(membro)


def _membros_do_zip(
    nome: str, caminho: Path, lim: Limites, orcamento: _Orcamento
) -> Iterator[ArquivoLido]:
    try:
        pacote = zipfile.ZipFile(caminho)
    except zipfile.BadZipFile as e:
        raise InventarioInvalidoError(f"{nome}: zip corrompido") from e
    membros = [
        m
        for m in sorted(pacote.infolist(), key=lambda i: i.filename)
        if not m.is_dir() and not m.filename.startswith("__MACOSX/")
    ]
    if len(membros) > lim.arquivos_no_zip:
        raise LimiteExcedidoError(f"{nome}: mais de {lim.arquivos_no_zip} arquivos no zip")
    if not membros:
        raise InventarioInvalidoError(f"{nome}: zip vazio")
    try:
        for m in membros:
            yield _um_arquivo(f"{nome}!{m.filename}", _abridor_de_membro(pacote, m), lim, orcamento)
    finally:
        pacote.close()


def _um_arquivo(
    nome: str, abrir: Callable[[], IO[bytes]], lim: Limites, orcamento: _Orcamento
) -> ArquivoLido:
    with abrir() as sonda:
        cabeca = sonda.read(2)
    fonte: IO[bytes] = abrir()
    if cabeca == _GZIP_MAGIC:
        fonte = cast(IO[bytes], gzip.GzipFile(fileobj=fonte))
    fluxo = io.BufferedReader(_LeitorContado(fonte, orcamento, nome), _BLOCO)
    texto = io.TextIOWrapper(fluxo, encoding="utf-8", errors="strict")
    try:
        return _decodificar(nome, texto, lim)
    except UnicodeDecodeError as e:
        texto.close()
        raise InventarioInvalidoError(f"{nome}: não é texto UTF-8") from e
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        texto.close()
        raise InventarioInvalidoError(f"{nome}: compactação corrompida") from e
    except BaseException:
        texto.close()
        raise


def _decodificar(nome: str, texto: io.TextIOWrapper, lim: Limites) -> ArquivoLido:
    primeira = _primeira_linha_util(texto)
    if primeira is None:
        texto.close()
        raise InventarioInvalidoError(f"{nome}: arquivo vazio")
    inicio = primeira.lstrip(_BOM).lstrip()
    if inicio.startswith("{"):
        registro = _tentar_objeto(inicio)
        if registro is not None:
            segunda = _primeira_linha_util(texto)
            if segunda is None:
                texto.close()
                return ArquivoLido(nome=nome, objeto=registro)
            # Duas linhas, cada uma um objeto completo: NDJSON.
            return ArquivoLido(
                nome=nome,
                itens=chain([registro], _linhas_ndjson(nome, segunda, texto)),
            )
    resto = texto.read(lim.bytes_documento_unico + 1)
    texto.close()
    corpo = inicio + resto
    if len(corpo.encode("utf-8")) > lim.bytes_documento_unico:
        raise LimiteExcedidoError(
            f"{nome}: documento JSON único acima de {lim.bytes_documento_unico // _MB} MB; "
            "exporte em NDJSON (uma linha por recurso)"
        )
    documento = _json(nome, corpo)
    if isinstance(documento, dict):
        return ArquivoLido(nome=nome, objeto=documento)
    if isinstance(documento, list):
        if not all(isinstance(i, dict) for i in documento):
            raise InventarioInvalidoError(f"{nome}: a lista precisa ter só objetos JSON")
        return ArquivoLido(nome=nome, itens=iter(documento))
    raise InventarioInvalidoError(f"{nome}: o JSON precisa ser um objeto ou uma lista")


def _primeira_linha_util(texto: io.TextIOWrapper) -> str | None:
    for linha in texto:
        if linha.strip().lstrip(_BOM):
            return linha
    return None


def _tentar_objeto(linha: str) -> dict[str, Any] | None:
    try:
        valor = json.loads(linha)
    except (json.JSONDecodeError, RecursionError):
        return None
    return valor if isinstance(valor, dict) else None


def _linhas_ndjson(nome: str, segunda: str, texto: io.TextIOWrapper) -> Iterator[dict[str, Any]]:
    numero = 2
    try:
        for linha in chain([segunda], texto):
            if not linha.strip():
                continue
            valor = _json(f"{nome} (linha {numero})", linha)
            if not isinstance(valor, dict):
                raise InventarioInvalidoError(f"{nome} (linha {numero}): esperado um objeto JSON")
            numero += 1
            yield valor
    except UnicodeDecodeError as e:
        raise InventarioInvalidoError(f"{nome}: não é texto UTF-8") from e
    except (OSError, EOFError) as e:
        raise InventarioInvalidoError(f"{nome}: compactação corrompida") from e
    finally:
        texto.close()


def _json(nome: str, texto: str) -> Any:
    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        raise InventarioInvalidoError(f"{nome}: JSON inválido na posição {e.pos}") from e
    except RecursionError as e:
        raise InventarioInvalidoError(f"{nome}: JSON aninhado demais") from e
