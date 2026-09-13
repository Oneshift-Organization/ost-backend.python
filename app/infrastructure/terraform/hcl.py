"""Escrita de HCL. Só daqui sai texto de Terraform.

## Por que não Jinja2

A especificação sugeria templates Jinja2. Optamos por escrever o HCL em Python,
por três razões concretas:

1. **O conjunto de recursos é fechado** (`TipoRecurso`, cinco entradas). Um
   motor de template genérico resolve um problema que não temos.
2. **Escape.** Jinja não conhece HCL: `{{ valor }}` num template interpola texto
   cru, e um valor com aspas quebra o arquivo — ou pior, injeta HCL. Aqui todo
   valor passa por `_literal`, e é a única porta.
3. **Uma dependência a menos** na imagem que gera código para o cliente.

## O que este módulo nunca emite

`provisioner`, `local-exec`, `remote-exec`, `backend` remoto e credencial. Não
por filtro — por não existir código que os escreva. É a diferença entre proibir
e não ter.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Identificador válido em HCL. Tudo que vira nome de recurso, variável ou chave
# de bloco passa por aqui antes de ser escrito sem aspas.
_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

INDENT = "  "


def identificador(valor: str) -> str:
    """Valida um nome que será escrito **sem aspas** no HCL.

    É a fronteira que impede um nome vindo de payload de virar sintaxe. Falhar
    aqui é melhor que gerar um arquivo que não parseia — ou que parseia e faz
    outra coisa.
    """
    v = str(valor).strip()
    if not _IDENTIFICADOR.match(v):
        raise ValueError(f"identificador HCL inválido: {valor!r}")
    return v


def _literal(valor: Any) -> str:
    """Converte um valor Python num literal HCL.

    Strings saem por `json.dumps`: a sintaxe de string do HCL é a do JSON para
    aspas e escapes, então aspas, barras e quebras de linha ficam corretas sem
    precisarmos reimplementar escaping.

    `${` é neutralizado. Sem isso, um valor vindo do billing — ou de um modelo —
    poderia virar interpolação e ler qualquer coisa do estado do Terraform.
    """
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if valor is None:
        return "null"
    if isinstance(valor, (int, float)):
        return json.dumps(valor)
    if isinstance(valor, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in valor) + "]"
    if isinstance(valor, dict):
        itens = ", ".join(f"{_literal(k)} = {_literal(v)}" for k, v in valor.items())
        return "{" + itens + "}"
    texto = str(valor).replace("${", "$${")
    return json.dumps(texto, ensure_ascii=False)


def referencia(expressao: str) -> _Bruto:
    """Marca uma expressão para sair **sem aspas** — `google_compute_network.x.id`.

    Só o nosso código constrói referências; nada vindo de payload ou de modelo
    passa por aqui. É o que separa "escrever HCL" de "interpolar texto".
    """
    return _Bruto(expressao)


class _Bruto(str):
    """Expressão HCL crua. Existe só para `_literal` saber não citar."""

    __slots__ = ()


def _valor(v: Any) -> str:
    return str(v) if isinstance(v, _Bruto) else _literal(v)


def atributo(nome: str, valor: Any, nivel: int = 1) -> str:
    return f"{INDENT * nivel}{identificador(nome)} = {_valor(valor)}"


def bloco(
    tipo: str,
    rotulos: list[str],
    corpo: list[str],
    nivel: int = 0,
) -> str:
    """Um bloco HCL: `tipo "rotulo" "rotulo" { corpo }`.

    Bloco vazio sai com `{}` numa linha só — é o que o `terraform fmt` faria.
    """
    prefixo = INDENT * nivel
    cabecalho = " ".join([identificador(tipo), *(_literal(r) for r in rotulos)])
    if not corpo:
        return f"{prefixo}{cabecalho} {{}}"
    linhas = "\n".join(corpo)
    return f"{prefixo}{cabecalho} {{\n{linhas}\n{prefixo}}}"


def bloco_aninhado(tipo: str, corpo: list[str], nivel: int = 1) -> str:
    """Bloco sem rótulo dentro de um recurso — `network_interface { ... }`."""
    prefixo = INDENT * nivel
    if not corpo:
        return f"{prefixo}{identificador(tipo)} {{}}"
    linhas = "\n".join(corpo)
    return f"{prefixo}{identificador(tipo)} {{\n{linhas}\n{prefixo}}}"


def comentario(texto: str, nivel: int = 0) -> str:
    """Comentário de várias linhas. O `#` é o estilo idiomático em HCL."""
    prefixo = INDENT * nivel
    return "\n".join(f"{prefixo}# {linha}".rstrip() for linha in texto.splitlines())


def arquivo(secoes: list[str]) -> str:
    """Junta seções, alinha os `=` e devolve o arquivo pronto.

    O alinhamento é o que o `terraform fmt` faria. Fazê-lo aqui tem um motivo
    prático: o arquivo já sai no formato canônico, então o `fmt` vira
    **conferência** em vez de correção — e o pacote não depende do binário do
    Terraform estar presente para sair bem formatado.
    """
    corpo = "\n\n".join(s for s in secoes if s.strip())
    return _alinhar(corpo.rstrip()) + "\n"


def _alinhar(texto: str) -> str:
    """Alinha os `=` de atribuições consecutivas no mesmo nível de indentação.

    Só agrupa linhas contíguas e de mesma indentação: um bloco aninhado no meio
    interrompe o grupo, que é exatamente o critério do `terraform fmt`.
    """
    linhas = texto.split("\n")
    saida: list[str] = []
    grupo: list[tuple[int, str, str, str]] = []

    def _descarregar() -> None:
        if not grupo:
            return
        largura = max(len(chave) for _, _, chave, _ in grupo)
        for _, prefixo, chave, valor in grupo:
            saida.append(f"{prefixo}{chave.ljust(largura)} = {valor}")
        grupo.clear()

    for linha in linhas:
        partes = _atribuicao(linha)
        if partes is None:
            _descarregar()
            saida.append(linha)
            continue
        prefixo, chave, valor = partes
        if grupo and grupo[-1][0] != len(prefixo):
            _descarregar()
        grupo.append((len(prefixo), prefixo, chave, valor))

    _descarregar()
    return "\n".join(saida)


def _atribuicao(linha: str) -> tuple[str, str, str] | None:
    """Separa `  chave = valor` em (indentação, chave, valor).

    Devolve ``None`` para qualquer outra coisa — comentário, abertura de bloco,
    linha em branco — e também quando o `=` está dentro de uma string, caso em
    que alinhar quebraria o valor.
    """
    if not linha.strip() or linha.lstrip().startswith("#"):
        return None
    prefixo = linha[: len(linha) - len(linha.lstrip())]
    resto = linha[len(prefixo) :]
    chave, separador, valor = resto.partition(" = ")
    if not separador or not _IDENTIFICADOR.match(chave.strip()):
        return None
    return prefixo, chave.strip(), valor
