"""Discos do escopo pelo inventário (PROXIMAS_EXECUCOES, item 14)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from app.domain.services.terraform_topologia.escopo import DiscoDoEscopo, recortar_ao_escopo
from app.schemas.terraform import TerraformBlueprint
from app.tests.unit.infrastructure.terraform_fixtures import com_topologia, entrada
from app.tests.unit.infrastructure.test_terraform_escopo_pacote import _recorte
from app.use_cases.terraform.build_blueprint import montar_blueprint

# O caso da f67fcd01: boot dos nós e volumes do Kubernetes, mais dois discos de dados
_DISCOS = (
    DiscoDoEscopo("vol-boot", 20, "ssd", boot=True),
    DiscoDoEscopo("pvc-loki", 10, "ssd", do_cluster=True),
    DiscoDoEscopo("pvc-kong", 2, "ssd", do_cluster=True),
    DiscoDoEscopo("dados-a", 100, "ssd"),
    DiscoDoEscopo("dados-b", 100, "ssd"),
    DiscoDoEscopo("arquivo", 500, "hdd"),
    DiscoDoEscopo("sem-tamanho", None, "ssd"),
)


def _plano(discos: tuple[DiscoDoEscopo, ...] | None) -> TerraformBlueprint:
    payload = com_topologia("aws")
    recorte = dataclasses.replace(
        _recorte(payload), custos={"armazenamento_ssd": Decimal("40")}, discos=discos
    )
    return montar_blueprint(recortar_ao_escopo(payload, recorte), entrada("oci"))


def _discos(blueprint: TerraformBlueprint) -> dict[int, int]:
    return {
        int(r.configuration["size_gb"]): int(r.configuration.get("volumes") or 1)
        for r in blueprint.resources
        if r.resource_type.value == "compute" and r.configuration.get("disk_only")
    }


def test_disco_pelo_tamanho_medido_sem_boot_nem_volume_do_cluster() -> None:
    blueprint = _plano(_DISCOS)
    assert _discos(blueprint) == {100: 2, 500: 1}
    assert any("2 volume(s) persistente(s) do Kubernetes (12 GB)" in a for a in blueprint.warnings)
    assert any("1 disco(s) do escopo sem tamanho" in a for a in blueprint.warnings)


def test_so_boot_e_volumes_do_cluster_nao_viram_disco() -> None:
    blueprint = _plano(_DISCOS[:3])
    assert _discos(blueprint) == {}


def test_sem_discos_no_recorte_fica_o_disco_pelo_custo() -> None:
    blueprint = _plano(None)
    estimados = [
        r
        for r in blueprint.resources
        if r.resource_type.value == "compute" and r.configuration.get("disk_only")
    ]
    assert estimados and all(r.configuration.get("size_source") == "estimado" for r in estimados)
