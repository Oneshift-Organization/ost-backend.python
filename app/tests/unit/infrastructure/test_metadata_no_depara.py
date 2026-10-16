"""A metadata curada (lock-in e refatoração) só chega à análise pelo nome do DE-PARA.

Lote v4, V4.4. Dez dos 40 serviços curados usavam um nome que o DE-PARA não tem
("Azure Virtual Machine", "Oracle Object Storage"): a nota nunca chegava à fatura nem
ao destino, e o serviço ficava "a confirmar".
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.domain.services.service_identity import service_key
from app.infrastructure.database.seed_service_metadata import METADATA_SEED, RENOMEADOS

_DE_PARA = (
    Path(__file__).resolve().parents[3]
    / "infrastructure"
    / "database"
    / "models"
    / "cloud_services_mapping.csv"
)

# Sem linha no DE-PARA: o Autonomous não está lá como banco relacional. Aguarda a
# curadoria decidir entre incluí-lo no DE-PARA ou tirá-lo da metadata.
_FORA_DO_DE_PARA = {("oci", "Oracle Autonomous Database")}


def _de_para() -> set[tuple[str, str]]:
    with _DE_PARA.open(encoding="utf-8") as arquivo:
        return {
            (linha["provider"].lower(), service_key(linha["provider"], linha["service_name"]))
            for linha in csv.DictReader(arquivo)
        }


def test_toda_metadata_curada_tem_linha_no_de_para() -> None:
    de_para = _de_para()
    fora = {
        (provedor, nome)
        for provedor, _, nome, _ in METADATA_SEED
        if (provedor, service_key(provedor, nome)) not in de_para
    }
    assert fora == _FORA_DO_DE_PARA


def test_nome_antigo_sai_do_seed_e_o_novo_e_do_de_para() -> None:
    de_para = _de_para()
    nomes = {(provedor, nome) for provedor, _, nome, _ in METADATA_SEED}
    for (provedor, antigo), novo in RENOMEADOS.items():
        assert (provedor, novo) in nomes
        assert (provedor, antigo) not in nomes
        assert (provedor, service_key(provedor, novo)) in de_para
