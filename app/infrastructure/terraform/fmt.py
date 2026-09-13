"""`terraform fmt` sobre os arquivos gerados — conferência, não correção.

## O que roda e o que não roda

Só `fmt`. **Não** rodamos `validate`, e a decisão é deliberada: `validate` exige
`terraform init`, que baixa o provider do Google — mais de 200 MB por execução,
com egress para a HashiCorp que o cluster pode não ter. `fmt` é formatador puro:
não toca a rede, não lê estado, não conhece provider.

`plan` e `apply` não existem aqui em nenhuma hipótese. A plataforma gera código.

## Por que ainda assim vale rodar

Os arquivos já saem no formato canônico (ver `hcl.arquivo`). O `fmt` serve como
verificação independente: se ele mudar alguma coisa, é porque o nosso gerador
divergiu do formato oficial — e é melhor descobrir isso aqui do que o cliente
descobrir num diff.

Se o binário não existir, a geração segue. O pacote não depende dele.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.schemas.terraform import ResumoValidacao

logger = logging.getLogger(__name__)

# `fmt` é local e rápido; acima disto alguma coisa está errada e é melhor
# devolver o pacote sem formatar do que segurar a requisição.
_TIMEOUT_SEGUNDOS = 30

_EXTENSOES_HCL = (".tf",)


def formatar(arquivos: dict[str, str]) -> tuple[dict[str, str], ResumoValidacao]:
    """Passa os `.tf` pelo `terraform fmt`. Devolve os arquivos e o resumo.

    Nunca levanta: uma falha de formatação não pode impedir a entrega de um
    pacote que já está correto. O motivo vai no resumo e daí para a tela.
    """
    binario = shutil.which("terraform")
    if binario is None:
        return arquivos, ResumoValidacao(
            formatado=False,
            validado=False,
            motivo_sem_validacao=(
                "binário do Terraform ausente na imagem; os arquivos já são "
                "gerados no formato canônico"
            ),
        )

    try:
        formatados = _rodar_fmt(binario, arquivos)
    except (subprocess.SubprocessError, OSError) as erro:
        logger.warning("terraform_fmt_falhou erro=%s", erro)
        return arquivos, ResumoValidacao(
            formatado=False,
            validado=False,
            motivo_sem_validacao=f"terraform fmt falhou: {erro}",
        )

    divergentes = [n for n in formatados if formatados[n] != arquivos[n]]
    if divergentes:
        # Não é erro para o cliente — o pacote sai formatado. Mas é sinal de que
        # o nosso gerador saiu do formato canônico, e queremos saber.
        logger.info("terraform_fmt_ajustou arquivos=%s", divergentes)

    return {**arquivos, **formatados}, ResumoValidacao(
        formatado=True,
        validado=False,
        motivo_sem_validacao=(
            "`terraform validate` exige `terraform init`, que baixa o provider "
            "da HashiCorp; fora do escopo desta versão"
        ),
    )


def _rodar_fmt(binario: str, arquivos: dict[str, str]) -> dict[str, str]:
    """Escreve os `.tf` num diretório temporário, formata e lê de volta.

    O diretório é apagado pelo contexto mesmo se o `fmt` falhar. Os argumentos
    vão como lista — nunca `shell=True`, nunca string concatenada.
    """
    hcl = {n: c for n, c in arquivos.items() if n.endswith(_EXTENSOES_HCL)}
    if not hcl:
        return {}

    with tempfile.TemporaryDirectory(prefix="ost-tf-") as tmp:
        base = Path(tmp)
        for nome, conteudo in hcl.items():
            # `nome` vem da allowlist do renderer, mas resolvemos e conferimos o
            # destino mesmo assim: é o que impede um nome com `..` de escrever
            # fora do diretório temporário.
            destino = (base / nome).resolve()
            if base.resolve() not in destino.parents:
                raise OSError(f"caminho fora do diretório de trabalho: {nome}")
            destino.write_text(conteudo, encoding="utf-8")

        subprocess.run(  # noqa: S603 - binário resolvido por which, args em lista
            [binario, "fmt", "-no-color", str(base)],
            check=False,
            capture_output=True,
            timeout=_TIMEOUT_SEGUNDOS,
            cwd=tmp,
        )

        return {
            nome: (base / nome).read_text(encoding="utf-8")
            for nome in hcl
        }
