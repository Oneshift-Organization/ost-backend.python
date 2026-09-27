"""Caracterização dos pacotes Terraform sem topologia (RI15, I8a).

A topologia do inventário muda o pacote só quando o payload a traz. Sem ela, o
pacote tem de sair **byte a byte** igual ao de antes: este teste congela o hash
de cada arquivo gerado para cinco payloads reais (os quatro mocks de billing e o
CSV real do GCP, que é o caminho legado, sem export detalhado) em cada destino.

Os payloads são fixos de propósito: foram capturados do pipeline em 27/09/2026 e
guardam só as chaves que o gerador lê. O teste é sobre o gerador, não sobre o
pipeline — recalcular o payload a cada execução faria o teste depender de preço
e câmbio do banco.

Mudou o pacote de propósito? Regenere o esperado e explique a mudança no commit:

    PYTHONPATH=. python -m app.tests.unit.infrastructure.test_terraform_caracterizacao
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.tests.unit.infrastructure.terraform_fixtures import FIXTURES, carregar, pacote

_ESPERADO = FIXTURES / "caracterizacao.json"

PAYLOADS = ("aws", "gcp", "azure", "oci", "gcp_legado")
DESTINOS = ("gcp", "aws", "azure", "oci")


def _pares() -> list[tuple[str, str]]:
    return [
        (nome, destino)
        for nome in PAYLOADS
        for destino in DESTINOS
        if destino != carregar(f"payload_{nome}")["provedor_origem"]
    ]


def _hashes(arquivos: dict[str, str]) -> dict[str, str]:
    return {
        nome: hashlib.sha256(conteudo.encode()).hexdigest()
        for nome, conteudo in sorted(arquivos.items())
    }


@pytest.mark.parametrize(("nome", "destino"), _pares())
def test_pacote_sem_topologia_nao_muda(nome: str, destino: str) -> None:
    esperado = json.loads(_ESPERADO.read_text())[f"{nome}>{destino}"]
    payload = carregar(f"payload_{nome}")
    assert "topologia" not in payload
    obtido = _hashes(pacote(payload, destino))
    diferentes = sorted(
        k for k in esperado.keys() | obtido.keys() if esperado.get(k) != obtido.get(k)
    )
    assert not diferentes, f"{nome}>{destino}: mudou {diferentes}"


def _regenerar() -> None:  # pragma: no cover - ferramenta de manutenção
    esperado = {f"{n}>{d}": _hashes(pacote(carregar(f"payload_{n}"), d)) for n, d in _pares()}
    _ESPERADO.write_text(json.dumps(esperado, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":  # pragma: no cover
    _regenerar()
