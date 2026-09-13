"""Empacota os arquivos gerados num ZIP.

Nada de `tempfile` nem de disco: o pacote inteiro tem poucos KB de texto e cabe
em memória. Um diretório temporário aqui seria mais uma coisa a limpar quando o
processo morre no meio.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

# Nomes que podem ir para dentro do ZIP. Qualquer outro é rejeitado — é a mesma
# allowlist do renderer, aplicada de novo na saída, porque um nome com `../`
# num ZIP é a vulnerabilidade clássica de extração (zip slip).
_PERMITIDOS = frozenset({
    "versions.tf",
    "providers.tf",
    "variables.tf",
    "main.tf",
    "outputs.tf",
    "terraform.tfvars.example",
    "README.md",
    "manifest.json",
    "validation.json",
})


class ArquivoNaoPermitidoError(ValueError):
    """Nome fora da allowlist tentou entrar no pacote."""


def montar_zip(arquivos: dict[str, str], *, raiz: str) -> bytes:
    """`{nome: conteúdo}` → bytes do ZIP, tudo sob um diretório `raiz`.

    A raiz existe para que extrair não espalhe arquivos no diretório atual de
    quem baixou — comportamento que irrita e, com nomes genéricos como
    `main.tf`, sobrescreve trabalho alheio.
    """
    raiz_limpa = _sanear_raiz(raiz)
    buffer = io.BytesIO()

    # Timestamp fixo por arquivo: sem isso, dois ZIPs do mesmo conteúdo gerados
    # em segundos diferentes têm bytes diferentes, e o pacote deixa de ser
    # comparável — que é metade do motivo de a geração ser determinística.
    data = datetime.now(UTC).timetuple()[:6]

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for nome in sorted(arquivos):
            if nome not in _PERMITIDOS:
                raise ArquivoNaoPermitidoError(nome)
            info = zipfile.ZipInfo(f"{raiz_limpa}/{nome}", date_time=data)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, arquivos[nome])

    return buffer.getvalue()


def _sanear_raiz(raiz: str) -> str:
    limpo = "".join(c if (c.isalnum() or c in "-_") else "-" for c in raiz.strip())
    limpo = limpo.strip("-") or "terraform"
    return limpo[:60]
