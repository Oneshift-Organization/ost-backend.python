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
    assert fora == set()


def test_nome_antigo_sai_do_seed_e_o_novo_e_do_de_para() -> None:
    de_para = _de_para()
    nomes = {(provedor, nome) for provedor, _, nome, _ in METADATA_SEED}
    for (provedor, antigo), novo in RENOMEADOS.items():
        assert (provedor, novo) in nomes
        assert (provedor, antigo) not in nomes
        assert (provedor, service_key(provedor, novo)) in de_para


def test_toda_metadata_tem_as_duas_notas_que_a_analise_le() -> None:
    for provedor, _, nome, metadata in METADATA_SEED:
        assert metadata["vendor_lock_in_level"] in {"low", "medium", "high"}, (provedor, nome)
        assert isinstance(metadata["requires_refactoring"], bool), (provedor, nome)


def test_um_nome_por_provedor() -> None:
    # O seed atualiza pela chave (provedor, nome): uma repetição sobrescreveria a outra
    chaves = [(provedor, nome) for provedor, _, nome, _ in METADATA_SEED]
    assert len(chaves) == len(set(chaves))
