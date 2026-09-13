"""`terraform validate` contra o schema real do provider.

## Por que está desligado por padrão

Não por custo. Medido em 13/09/2026, com `TF_PLUGIN_CACHE_DIR` quente:

    terraform init -backend=false   0,86s
    terraform validate -json        0,35s

O provider do Google são 99 MB baixados **uma vez** e reaproveitados. A estimativa
inicial de "200 MB por geração" estava errada — ela vale só sem cache, e é
exatamente esse o caso que a checagem de `TF_PLUGIN_CACHE_DIR` abaixo recusa.

Fica desligado porque ligar exige três coisas do ambiente que ainda não existem:
o binário na imagem, um volume persistente para o cache, e egress para
`registry.terraform.io` no primeiro uso.

## O que isto pega e o `fmt` não

`fmt` só olha sintaxe. `validate` compara contra o schema do provider — atributo
que não existe, tipo errado, bloco no lugar errado. Foi rodando isto à mão que
apareceram dois bugs reais: nome de recurso com `_` (o GCP recusa) e `labels`
escrito como bloco em vez de atributo.

`plan` e `apply` não existem aqui. Nunca.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.schemas.terraform import ResumoValidacao

logger = logging.getLogger(__name__)

_CACHE_ENV = "TF_PLUGIN_CACHE_DIR"


def validar(arquivos: dict[str, str], *, formatado: bool) -> ResumoValidacao:
    """Roda `init -backend=false` + `validate -json` num diretório temporário.

    Nunca levanta. Um pacote correto não pode deixar de ser entregue porque a
    validação não pôde rodar — o motivo vai no resumo e daí para a tela.
    """
    settings = get_settings()

    if not settings.terraform_validate_enabled:
        return ResumoValidacao(
            formatado=formatado,
            validado=False,
            motivo_sem_validacao="validação desabilitada neste ambiente",
        )

    binario = shutil.which("terraform")
    if binario is None:
        return ResumoValidacao(
            formatado=formatado,
            validado=False,
            motivo_sem_validacao="binário do Terraform ausente na imagem",
        )

    if not os.environ.get(_CACHE_ENV):
        # Sem cache o `init` rebaixa o provider inteiro a cada geração. Recusar
        # é melhor que aceitar: com carga, isso satura a rede do cluster e
        # transforma uma feature barata numa cara sem ninguém perceber.
        return ResumoValidacao(
            formatado=formatado,
            validado=False,
            motivo_sem_validacao=(
                f"{_CACHE_ENV} não configurado; sem cache de provider cada "
                "geração baixaria ~99 MB"
            ),
        )

    try:
        return _rodar(binario, arquivos, formatado, settings.terraform_cli_timeout_seconds)
    except (subprocess.SubprocessError, OSError) as erro:
        logger.warning("terraform_validate_falhou erro=%s", erro)
        return ResumoValidacao(
            formatado=formatado,
            validado=False,
            motivo_sem_validacao=f"validação não pôde rodar: {erro}",
        )


def _rodar(
    binario: str, arquivos: dict[str, str], formatado: bool, timeout: int
) -> ResumoValidacao:
    hcl = {n: c for n, c in arquivos.items() if n.endswith(".tf")}
    if not hcl:
        return ResumoValidacao(
            formatado=formatado, validado=False, motivo_sem_validacao="nada a validar"
        )

    with tempfile.TemporaryDirectory(prefix="ost-tf-val-") as tmp:
        base = Path(tmp)
        for nome, conteudo in hcl.items():
            destino = (base / nome).resolve()
            if base.resolve() not in destino.parents:
                raise OSError(f"caminho fora do diretório de trabalho: {nome}")
            destino.write_text(conteudo, encoding="utf-8")

        init = _executar(
            [binario, "init", "-backend=false", "-input=false", "-no-color"],
            tmp,
            timeout,
        )
        if init.returncode != 0:
            return ResumoValidacao(
                formatado=formatado,
                validado=False,
                erros=[_curto(init.stderr or init.stdout)],
                motivo_sem_validacao="`terraform init` falhou",
            )

        res = _executar([binario, "validate", "-json", "-no-color"], tmp, timeout)
        return _interpretar(res.stdout, formatado)


def _executar(
    argumentos: list[str], cwd: str, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Argumentos em lista, nunca `shell=True`, sempre com timeout.

    `-backend=false` é o que garante que nenhum estado remoto é tocado, e
    `-input=false` impede que um prompt interativo trave o processo esperando
    alguém digitar.
    """
    return subprocess.run(  # noqa: S603 - binário do `which`, argumentos em lista
        argumentos,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


def _interpretar(saida: str, formatado: bool) -> ResumoValidacao:
    try:
        bruto = json.loads(saida or "{}")
    except json.JSONDecodeError:
        return ResumoValidacao(
            formatado=formatado,
            validado=False,
            motivo_sem_validacao="saída do validate não era JSON",
        )

    erros = [
        f"{d.get('summary', '')}: {d.get('detail', '')}".strip(": ")
        for d in bruto.get("diagnostics") or []
        if d.get("severity") == "error"
    ]
    return ResumoValidacao(
        formatado=formatado,
        validado=bool(bruto.get("valid")),
        erros=[_curto(e) for e in erros],
        motivo_sem_validacao=None if bruto.get("valid") else "configuração inválida",
    )


def _curto(texto: str) -> str:
    """O texto vai para a tela do cliente. Sem stack, sem caminho de arquivo
    temporário nosso."""
    return " ".join((texto or "").split())[:400]
