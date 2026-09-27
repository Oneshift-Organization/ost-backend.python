"""Leitura segura dos arquivos de inventário (RNF4)."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.infrastructure.inventory_ingestion.leitura import (
    InventarioInvalidoError,
    LimiteExcedidoError,
    Limites,
    ler_arquivos,
)


def _ler(*caminhos: Path, limites: Limites | None = None):
    lidos = []
    for a in ler_arquivos([(c.name, c) for c in caminhos], limites):
        itens = list(a.itens) if a.itens is not None else None
        lidos.append((a.nome, a.objeto, itens))
    return lidos


def test_objeto_json_formatado(tmp_path) -> None:
    arq = tmp_path / "snap.json"
    arq.write_text(json.dumps({"configurationItems": [{"a": 1}]}, indent=2))
    [(nome, objeto, itens)] = _ler(arq)
    assert nome == "snap.json"
    assert objeto == {"configurationItems": [{"a": 1}]}
    assert itens is None


def test_objeto_json_numa_linha_so(tmp_path) -> None:
    arq = tmp_path / "snap.json"
    arq.write_text(json.dumps({"data": []}))
    [(_, objeto, itens)] = _ler(arq)
    assert objeto == {"data": []}
    assert itens is None


def test_array_json(tmp_path) -> None:
    arq = tmp_path / "lista.json"
    arq.write_text(json.dumps([{"a": 1}, {"a": 2}], indent=1))
    [(_, objeto, itens)] = _ler(arq)
    assert objeto is None
    assert itens == [{"a": 1}, {"a": 2}]


def test_ndjson_com_linhas_em_branco_e_bom(tmp_path) -> None:
    arq = tmp_path / "assets.json"
    arq.write_text('﻿{"a": 1}\n\n{"a": 2}\n{"a": 3}\n', encoding="utf-8")
    [(_, objeto, itens)] = _ler(arq)
    assert objeto is None
    assert itens == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_gzip_reconhecido_pelo_conteudo_mesmo_com_extensao_errada(tmp_path) -> None:
    arq = tmp_path / "snapshot.json"  # sem .gz
    arq.write_bytes(gzip.compress(json.dumps({"configurationItems": []}).encode()))
    [(_, objeto, _)] = _ler(arq)
    assert objeto == {"configurationItems": []}


def test_zip_com_paginas_e_gzip_dentro(tmp_path) -> None:
    arq = tmp_path / "paginas.zip"
    with zipfile.ZipFile(arq, "w") as z:
        z.writestr("b.json", json.dumps({"data": [{"id": "b"}]}))
        z.writestr("a.json.gz", gzip.compress(json.dumps({"data": [{"id": "a"}]}).encode()))
        z.writestr("__MACOSX/lixo", "x")
        z.writestr("pasta/", "")
    lidos = _ler(arq)
    assert [n for n, _, _ in lidos] == ["paginas.zip!a.json.gz", "paginas.zip!b.json"]
    assert lidos[0][1] == {"data": [{"id": "a"}]}


@pytest.mark.parametrize(
    ("conteudo", "mensagem"),
    [
        (b"", "vazio"),
        (b"   \n\n", "vazio"),
        (b"{ nao e json", "JSON inválido"),
        (b"\xff\xfe\x00{", "UTF-8"),
        (b'"texto solto"', "objeto ou uma lista"),
        (b"[1, 2]", "só objetos"),
        (b'{"a": 1}\n[2]\n', "esperado um objeto"),
    ],
)
def test_conteudo_invalido(tmp_path, conteudo, mensagem) -> None:
    arq = tmp_path / "x.json"
    arq.write_bytes(conteudo)
    with pytest.raises(InventarioInvalidoError, match=mensagem):
        _ler(arq)


def test_json_aninhado_demais(tmp_path) -> None:
    arq = tmp_path / "x.json"
    arq.write_text("[" * 200_000 + "]" * 200_000)
    with pytest.raises(InventarioInvalidoError):
        _ler(arq)


def test_gzip_corrompido(tmp_path) -> None:
    arq = tmp_path / "x.json.gz"
    arq.write_bytes(gzip.compress(b'{"a": 1}')[:-8] + b"\x00" * 4)
    with pytest.raises(InventarioInvalidoError):
        _ler(arq)


def test_zip_corrompido_e_vazio(tmp_path) -> None:
    ruim = tmp_path / "ruim.zip"
    ruim.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    with pytest.raises(InventarioInvalidoError, match="zip corrompido"):
        _ler(ruim)
    vazio = tmp_path / "vazio.zip"
    with zipfile.ZipFile(vazio, "w"):
        pass
    with pytest.raises(InventarioInvalidoError, match="zip vazio"):
        _ler(vazio)


def test_limite_de_documento_unico(tmp_path) -> None:
    arq = tmp_path / "grande.json"
    arq.write_text(json.dumps({"x": "a" * 5000}, indent=1))
    with pytest.raises(LimiteExcedidoError, match="NDJSON"):
        _ler(arq, limites=Limites(bytes_documento_unico=1000))


def test_ndjson_nao_tem_teto_de_documento_unico(tmp_path) -> None:
    arq = tmp_path / "grande.ndjson"
    arq.write_text("\n".join(json.dumps({"i": i, "p": "a" * 100}) for i in range(100)))
    [(_, _, itens)] = _ler(arq, limites=Limites(bytes_documento_unico=1000))
    assert len(itens) == 100


def test_orcamento_total_para_zip_bomb(tmp_path) -> None:
    # 5 MB de zeros compactam em poucos KB: o limite é sobre o descompactado.
    arq = tmp_path / "bomba.json.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as g:
        g.write(b'{"a": "' + b"0" * 5_000_000 + b'"}')
    arq.write_bytes(buf.getvalue())
    assert arq.stat().st_size < 50_000
    with pytest.raises(LimiteExcedidoError, match="descompactados"):
        _ler(arq, limites=Limites(bytes_total=1_000_000))


def test_orcamento_e_compartilhado_entre_arquivos(tmp_path) -> None:
    a = tmp_path / "a.ndjson"
    b = tmp_path / "b.ndjson"
    linhas = "\n".join(json.dumps({"i": i, "p": "x" * 50}) for i in range(20))
    a.write_text(linhas)
    b.write_text(linhas)
    tamanho = len(linhas.encode())
    _ler(a, limites=Limites(bytes_total=tamanho + 10))
    with pytest.raises(LimiteExcedidoError):
        _ler(a, b, limites=Limites(bytes_total=tamanho + 10))


def test_limite_de_membros_no_zip(tmp_path) -> None:
    arq = tmp_path / "muitos.zip"
    with zipfile.ZipFile(arq, "w") as z:
        for i in range(4):
            z.writestr(f"{i}.json", "{}")
    with pytest.raises(LimiteExcedidoError, match="mais de 3"):
        _ler(arq, limites=Limites(arquivos_no_zip=3))
