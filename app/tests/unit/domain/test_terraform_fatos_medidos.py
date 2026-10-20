"""Terraform a partir de fatos medidos (E8 do PLANO_CUR_BIGQUERY).

Com export detalhado, disco e banco deixam de ser estimados pelo custo: o
tamanho e a classe vêm do billing. O pacote precisa dizer isso — e continuar
honesto sobre o que ainda não sabe (versão do motor, cache, snapshot).
"""

from __future__ import annotations

import re
from copy import deepcopy
from uuid import uuid4

from app.domain.services.terraform_plan import derivar_plano
from app.infrastructure.terraform.renderers import renderizar
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint


def _payload() -> dict:
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "pools_computacao": [
            {
                "familia": "T3A",
                "capacidade": {"vcpus": 103.0, "ram_gib": 230.0},
                "custo_computacao": 1100.0,
                "instancias_medidas": 91,
                "tipos_origem": {"t3a.medium": 42, "t3a.small": 22},
                "fracao_tempo_ativo": 0.49,
                "fonte_capacidade": "medida",
                "equivalentes": [
                    {
                        "provedor": "GCP",
                        "nome_servico": "Compute Engine E2",
                        "sku": "e2-standard-8",
                        "instancias": 13,
                        "correspondencia_tecnica": {"vcpus": 104.0, "ram_gb": 416.0},
                        "financeiro": {"confianca_preco": "official"},
                    }
                ],
            }
        ],
        "custos_auxiliares": {
            "armazenamento_ssd": 5377.0,
            "armazenamento_hdd": 376.0,  # só snapshot no mock
            "armazenamento_objetos": 6324.0,
            "banco_dados": 30689.0,
        },
        "inventario_medido": {
            "horas_periodo": 744.0,
            "armazenamento": {
                "armazenamento_ssd": {"gb": 48626, "recursos": 99},
                "armazenamento_objetos": {"gb": 307564, "recursos": 24},
                "snapshots": {"gb": 7406, "recursos": 9, "custo": {"armazenamento_hdd": 376.0}},
            },
            "bancos": [
                {"classe": "db.t4g.small", "motor": "PostgreSQL", "multi_az": True,
                 "regiao": "us-east-1", "quantidade": 38, "custo": 5803.0},
                {"classe": "db.r8g.16xlarge", "motor": "Aurora PostgreSQL", "multi_az": False,
                 "regiao": "us-east-1", "quantidade": 1, "custo": 5759.0},
                {"classe": "cache.t4g.medium", "motor": "Redis", "multi_az": False,
                 "regiao": "us-east-1", "quantidade": 17, "custo": 900.0},
                {"classe": "db.t3.micro", "motor": "MySQL", "multi_az": True,
                 "regiao": "us-east-1", "quantidade": 1, "custo": 25.0},
            ],
            "total_bancos": 60,
        },
    }


def _derivar(payload: dict | None = None, destino: str = "gcp", ha: bool = True):
    return derivar_plano(
        payload or _payload(),
        provedor_destino=destino,
        regiao="us-central1",
        alta_disponibilidade=ha,
    )


def _recurso(plano, prefixo: str):
    return [r for r in plano.recursos if r.logical_name.startswith(prefixo)]


# ── armazenamento ───────────────────────────────────────────────────────────


def test_disco_medido_vira_volumes_com_count() -> None:
    plano = _derivar()
    [disco] = _recurso(plano, "disco_storage_ssd")
    assert disco.configuration["volumes"] == 99
    assert disco.configuration["size_gb"] == 492  # ceil(48626 / 99)
    assert disco.configuration["size_source"] == "medido"
    premissa = next(p for p in plano.premissas if p.campo == "disco_storage_ssd.size_gb")
    assert premissa.categoria == "derivacao"
    assert "99 volumes" in premissa.valor


def test_snapshot_nao_vira_disco() -> None:
    plano = _derivar()
    # O HDD do mock é só snapshot: com inventário medido e sem GB de volume,
    # estimar pelo custo inventaria um disco.
    assert not _recurso(plano, "disco_storage_hdd")
    assert any("Snapshots (7,406 GB em 9 recursos" in x for x in plano.nao_suportado)


def test_balde_misto_estima_so_o_que_nao_e_snapshot() -> None:
    payload = _payload()
    payload["custos_auxiliares"]["armazenamento_hdd"] = 826.0  # 376 de snapshot + 450
    plano = _derivar(payload)
    [disco] = _recurso(plano, "disco_storage_hdd")
    assert disco.configuration["size_source"] == "estimado"
    premissa = next(p for p in plano.premissas if p.campo == "disco_storage_hdd.size_gb")
    assert "450" in premissa.base  # só o residual, sem o snapshot


def test_bucket_com_volume_medido() -> None:
    [bucket] = _recurso(_derivar(), "bucket_object_storage")
    assert bucket.configuration["estimated_gb"] == 307564
    assert bucket.configuration["size_source"] == "medido"


def test_sem_inventario_segue_estimando() -> None:
    payload = _payload()
    del payload["inventario_medido"]
    plano = _derivar(payload)
    [disco] = _recurso(plano, "disco_storage_ssd")
    assert disco.configuration["size_source"] == "estimado"
    assert "volumes" not in disco.configuration
    premissa = next(p for p in plano.premissas if p.campo == "disco_storage_ssd.size_gb")
    assert premissa.categoria == "estimativa"


# ── banco ───────────────────────────────────────────────────────────────────


def test_um_recurso_por_grupo_com_a_contagem() -> None:
    plano = _derivar()
    bancos = _recurso(plano, "banco_")
    por_classe = {b.configuration["source_class"]: b.configuration for b in bancos}
    assert por_classe["db.t4g.small"]["instances"] == 38
    assert por_classe["db.t4g.small"]["vcpus"] == 2
    assert por_classe["db.r8g.16xlarge"]["vcpus"] == 64
    # Aurora vira a família base: o destino não tem Aurora.
    assert por_classe["db.r8g.16xlarge"]["version_var"] == "database_version_postgresql"
    assert por_classe["db.t3.micro"]["version_var"] == "database_version_mysql"


def test_cache_fica_fora_e_e_declarado() -> None:
    plano = _derivar()
    assert not any("cache" in r.configuration.get("source_class", "") for r in plano.recursos)
    assert any("Cache gerenciado (17 nós" in x for x in plano.nao_suportado)


def test_azure_so_gera_postgresql_e_declara_o_resto() -> None:
    plano = _derivar(destino="azure")
    classes = {r.configuration.get("source_class") for r in _recurso(plano, "banco_")}
    assert "db.t3.micro" not in classes  # MySQL
    assert any("só é gerado para postgresql" in x and "MySQL" in x for x in plano.nao_suportado)


def test_oci_gera_o_banco_do_motor_medido() -> None:
    """Cada motor no produto dele: PostgreSQL e MySQL não viram Oracle (Autonomous)."""
    assert not any("troca de motor" in x for x in _derivar(destino="oci").avisos)
    main = _pacote("oci")["main.tf"]
    assert "oci_database_autonomous_database" not in main
    postgres = re.findall(r'resource "oci_psql_db_system" "(\w+)"', main)
    assert len(postgres) == 2  # db.t4g.small e o Aurora PostgreSQL
    assert re.search(r'resource "oci_mysql_mysql_db_system"', main)
    # OCPU = vCPU ÷ 2 (a conta da cotação); memória da classe, no mínimo 16 GB
    assert re.search(r"instance_ocpu_count\s*=\s*32\b", main)  # db.r8g.16xlarge: 64 vCPU
    assert re.search(r"instance_memory_size_in_gbs\s*=\s*512\b", main)
    assert re.search(r"instance_ocpu_count\s*=\s*1\b", main)  # db.t4g.small: 2 vCPU, 2 GB
    assert re.search(r"instance_memory_size_in_gbs\s*=\s*16\b", main)
    assert re.search(r'shape_name\s*=\s*"MySQL.2"', main)  # db.t3.micro: 2 vCPU = 2 ECPU
    assert re.search(r"mysql_version\s*=\s*var\.database_version_mysql", main)
    assert re.search(r"db_version\s*=\s*var\.database_version_postgresql", main)
    assert 'variable "db_admin_username"' in _pacote("oci")["variables.tf"]


def test_oci_alta_disponibilidade_do_motor() -> None:
    main = _pacote("oci", ha=True)["main.tf"]
    assert re.search(r"instance_count\s*=\s*2\b", main)  # PostgreSQL: 2 nós
    assert re.search(r"is_highly_available\s*=\s*true", main)  # HeatWave


def test_oci_sem_sql_server_gerenciado_declara() -> None:
    payload = _payload()
    payload["inventario_medido"]["bancos"][0]["motor"] = "SQL Server"
    plano = _derivar(payload, destino="oci")
    assert any("SQL Server" in x and "OCI" in x for x in plano.nao_suportado)


def test_grupos_nao_detalhados_sao_avisados() -> None:
    # 60 no total, 57 nos grupos listados.
    assert any("3 instâncias de banco em grupos menores" in x for x in _derivar().avisos)


def test_multi_az_sem_ha_e_avisado() -> None:
    assert any("Multi-AZ" in x for x in _derivar(ha=False).avisos)


# ── compute ─────────────────────────────────────────────────────────────────


def test_consolidacao_e_tempo_ativo_na_premissa() -> None:
    plano = _derivar()
    premissa = next(p for p in plano.premissas if p.campo.endswith(".machine_type"))
    assert "Origem medida: 91 instâncias" in premissa.base
    assert "49%" in premissa.base
    assert any("91 instâncias na origem viram 13" in x for x in plano.avisos)


# ── variáveis e renderização ────────────────────────────────────────────────


def _pacote(destino: str, *, motor: str | None = None, ha: bool = False) -> dict[str, str]:
    entrada = TerraformGenerationIn(
        analysis_job_id=uuid4(), project_name="teste-medido", target_provider=destino,
        region={"gcp": "us-central1", "aws": "sa-east-1", "azure": "brazilsouth",
                "oci": "sa-saopaulo-1"}[destino],
        availability={"high_availability": ha},
    )
    payload = deepcopy(_payload())
    payload["provedor_origem"] = "gcp" if destino == "aws" else "aws"
    if motor:
        for grupo in payload["inventario_medido"]["bancos"]:
            if grupo["motor"] != "Redis":
                grupo["motor"] = motor
    return renderizar(montar_blueprint(payload, entrada))


def test_variavel_de_versao_por_motor() -> None:
    variaveis = _pacote("gcp")["variables.tf"]
    assert 'variable "database_version_postgresql"' in variaveis
    assert 'variable "database_version_mysql"' in variaveis
    assert "motor medido na origem" in variaveis


def test_renderers_usam_count_e_nome_unico() -> None:
    for destino in ("gcp", "aws", "azure", "oci"):
        main = _pacote(destino)["main.tf"]
        assert re.search(r"count\s*=\s*99\b", main), destino
        assert "${count.index}" in main, destino
        assert "TAMANHO MEDIDO" in main, destino
        assert "TAMANHO ESTIMADO" not in main, destino


def test_aws_motor_medido_vira_literal() -> None:
    main = _pacote("aws")["main.tf"]
    assert re.search(r'engine\s*=\s*"postgres"', main)
    assert re.search(r"engine_version\s*=\s*var\.database_version_postgresql", main)


def test_oci_db_name_cabe_em_14_e_nao_colide() -> None:
    # `db_name` é do Autonomous: só a origem Oracle o gera
    main = _pacote("oci", motor="Oracle")["main.tf"]
    assert "oci_psql_db_system" not in main
    nomes = [
        linha.split("=", 1)[1].strip().strip('"').replace("${count.index}", "")
        for linha in main.splitlines()
        if linha.strip().startswith("db_name")
    ]
    assert len(nomes) == len(set(nomes)) > 1
    assert all(len(n) + 3 <= 14 for n in nomes)
