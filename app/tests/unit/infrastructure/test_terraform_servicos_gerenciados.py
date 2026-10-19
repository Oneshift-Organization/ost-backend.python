"""Kubernetes, registro de imagens, segredos e DNS no Terraform do escopo
(PROXIMAS_EXECUCOES, itens 15 e 19), nos quatro destinos."""

from __future__ import annotations

import os
import re
import shutil
from decimal import Decimal
from typing import Any

import pytest

from app.core import config
from app.domain.services.terraform_topologia.escopo import (
    ClusterDoEscopo,
    ComponenteDoEscopo,
    GrupoDeNos,
    RecorteDoEscopo,
    recortar_ao_escopo,
)
from app.infrastructure.terraform.validate import validar
from app.tests.unit.infrastructure.terraform_fixtures import com_topologia, entrada, pacote
from app.use_cases.terraform.build_blueprint import montar_blueprint

_PARES = (("aws", "oci"), ("aws", "gcp"), ("aws", "azure"), ("azure", "aws"))

_CLUSTER = {
    "oci": ("oci_containerengine_cluster", "oci_containerengine_node_pool"),
    "aws": ("aws_eks_cluster", "aws_eks_node_group"),
    "azure": ("azurerm_kubernetes_cluster", "azurerm_kubernetes_cluster_node_pool"),
    "gcp": ("google_container_cluster", "google_container_node_pool"),
}
_VM = {
    "oci": "oci_core_instance",
    "aws": "aws_instance",
    "azure": "azurerm_linux_virtual_machine",
    "gcp": "google_compute_instance",
}
# (registro, segredo, zona) e quantos de cada saem para 2 repositórios e 2 segredos
_COMPONENTES = {
    "oci": (("oci_artifacts_container_repository", 2), ("oci_kms_vault", 1), ("oci_dns_zone", 1)),
    "aws": (("aws_ecr_repository", 2), ("aws_secretsmanager_secret", 2), ("aws_route53_zone", 1)),
    "azure": (
        ("azurerm_container_registry", 1),
        ("azurerm_key_vault", 1),
        ("azurerm_private_dns_zone", 1),
    ),
    "gcp": (
        ("google_artifact_registry_repository", 2),
        ("google_secret_manager_secret", 2),
        ("google_dns_managed_zone", 1),
    ),
}


def _escopo(origem: str) -> dict[str, Any]:
    """O primeiro pool da conta é o cluster inteiro; o segundo, metade nó e metade VM."""
    payload = com_topologia(origem)
    posicionados = [
        p for p in payload["topologia"]["detalhe"]["posicionamento"] if p.get("posicionamentos")
    ]
    primeiro, segundo = posicionados[0]["familia"].upper(), posicionados[1]["familia"].upper()
    # As máquinas dos dois pools estão no escopo
    maquinas = {
        (f["subrede"], p["familia"].upper()): int(f["instancias"])
        for p in posicionados[:2]
        for f in p["posicionamentos"]
    }
    recorte = RecorteDoEscopo(
        aplicacoes=("loja",),
        unidades=1,
        maquinas=maquinas,
        bancos=frozenset(),
        bancos_por_classe={},
        custos={},
        clusters=(
            ClusterDoEscopo(
                nome="eks-loja",
                versao="1.30",
                grupos=(
                    GrupoDeNos("ng-app", primeiro, None, maquinas=3, da_familia=3),
                    GrupoDeNos("ng-infra", segundo, None, maquinas=1, da_familia=2),
                ),
                balanceadores=("k8s-ingress-loja",),
            ),
        ),
        componentes=(
            ComponenteDoEscopo("registro_de_imagens", "loja/api"),
            ComponenteDoEscopo("registro_de_imagens", "loja/web"),
            ComponenteDoEscopo("segredo", "loja-db"),
            ComponenteDoEscopo("segredo", "loja-api-key"),
            ComponenteDoEscopo("zona_dns", "loja.internal."),
        ),
    )
    return recortar_ao_escopo(payload, recorte)


def _conta(main: str, tipo: str) -> int:
    return len(re.findall(rf'resource "{tipo}" ', main))


@pytest.mark.parametrize(("origem", "destino"), _PARES)
def test_cluster_com_os_grupos_de_nos_no_lugar_das_vms(origem: str, destino: str) -> None:
    payload = _escopo(origem)
    blueprint = montar_blueprint(payload, entrada(destino))
    [cluster] = [r for r in blueprint.resources if r.resource_type.value == "kubernetes_cluster"]
    grupos = {g["name"]: g for g in cluster.configuration["node_pools"]}
    assert set(grupos) == {"ng-app", "ng-infra"}
    assert cluster.configuration["service_balancers"] == ["k8s-ingress-loja"]
    familias = {g["source_family"] for g in grupos.values()}
    # O pool inteiro no cluster some das VMs; o pool só em parte fica com o resto
    vms = [
        r
        for r in blueprint.resources
        if r.resource_type.value == "compute" and not r.configuration.get("disk_only")
    ]
    restantes = {str(r.configuration.get("source_family")).upper() for r in vms}
    assert grupos["ng-app"]["source_family"] not in restantes
    assert familias <= {str(g["source_family"]) for g in grupos.values()}
    assert not any(x.startswith("Kubernetes:") for x in blueprint.unsupported_items)
    main = pacote(payload, destino)["main.tf"]
    tipo_cluster, tipo_pool = _CLUSTER[destino]
    assert _conta(main, tipo_cluster) == 1
    # A Azure leva o primeiro grupo dentro do cluster (default_node_pool)
    assert _conta(main, tipo_pool) == (1 if destino == "azure" else 2)
    assert "nasce do Service do cluster" in main


@pytest.mark.parametrize(("origem", "destino"), _PARES)
def test_registro_cofre_e_zona_conforme_o_destino(origem: str, destino: str) -> None:
    arquivos = pacote(_escopo(origem), destino)
    main = arquivos["main.tf"]
    for tipo, quantos in _COMPONENTES[destino]:
        assert _conta(main, tipo) == quantos, tipo
    assert "o valor nunca viaja pelo pacote" in main
    assert "As imagens não viajam" in main
    assert "loja.internal" in main
    assert "loja.internal.." not in main


def test_versao_do_kubernetes_onde_o_destino_aceita_a_maior() -> None:
    aws = pacote(_escopo("aws"), "gcp")["variables.tf"]
    assert re.search(r'variable "kubernetes_version" \{[^}]*default\s*=\s*"1.30"', aws)
    oci = pacote(_escopo("aws"), "oci")["variables.tf"]
    # A OCI pede a versão completa: sem default
    bloco = re.search(r'variable "kubernetes_version" \{[^}]*\}', oci)
    assert bloco is not None
    assert "default" not in bloco.group(0)


def test_sem_cluster_nem_componente_o_pacote_nao_muda() -> None:
    payload = com_topologia("aws")
    sem = recortar_ao_escopo(
        payload,
        RecorteDoEscopo(
            aplicacoes=("loja",),
            unidades=1,
            maquinas={},
            bancos=frozenset(),
            bancos_por_classe={},
            custos={"armazenamento_ssd": Decimal("40")},
        ),
    )
    main = pacote(sem, "oci")["main.tf"]
    assert "oci_containerengine_cluster" not in main
    assert "kubernetes_version" not in pacote(sem, "oci")["variables.tf"]


@pytest.mark.skipif(
    shutil.which("terraform") is None or not os.environ.get("TF_PLUGIN_CACHE_DIR"),
    reason="precisa do binário do Terraform e de TF_PLUGIN_CACHE_DIR",
)
@pytest.mark.parametrize(("origem", "destino"), _PARES)
def test_servicos_gerenciados_passam_no_validate(
    monkeypatch: pytest.MonkeyPatch, origem: str, destino: str
) -> None:
    monkeypatch.setenv("TERRAFORM_VALIDATE_ENABLED", "true")
    config.get_settings.cache_clear()
    try:
        r = validar(pacote(_escopo(origem), destino), formatado=True)
    finally:
        config.get_settings.cache_clear()
    assert r.validado is True, r.erros
    assert _VM[destino]  # o destino conhecido
