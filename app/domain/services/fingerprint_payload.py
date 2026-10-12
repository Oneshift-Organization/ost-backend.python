"""Hash de conteúdo JSON, estável entre processos e máquinas.

É a identidade do snapshot do agente, da representação entregue a ele e de cada
envio (PLANO_SNAPSHOT_AGENTE, D4 e D9). Por isso o JSON é canônico: chaves
ordenadas, sem espaços, UTF-8. Duas montagens do mesmo conteúdo dão o mesmo hash
em qualquer processo, o que foi verificado com `PYTHONHASHSEED` diferentes.

Só aceita tipos nativos de JSON. Um `Decimal` ou `datetime` no meio do payload
levanta `TypeError` em vez de virar texto em silêncio: se virasse, o hash
calculado aqui deixaria de bater com o JSON que o cliente recebe.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def json_canonico(valor: Any) -> str:
    return json.dumps(
        valor, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def hash_canonico(valor: Any) -> str:
    """SHA-256 em hexadecimal (64 caracteres) do JSON canônico."""
    return hashlib.sha256(json_canonico(valor).encode("utf-8")).hexdigest()
