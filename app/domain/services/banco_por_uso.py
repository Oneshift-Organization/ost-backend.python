"""Banco gerenciado por partes: instância, cache, armazenamento, backup (PLANO_PRECISAO_OCI).

Antes, o custo inteiro de banco era repreçado pela razão de uma taxa de referência por
vCPU-mês (OCI 30 contra AWS 62), e a OCI saía pela metade do preço real. Depois, a
instância passou a ser cotada pela vCPU-hora (P1.6); a régua do mock CUR mostrou que o
resto ainda pesava errado (B1 a B4): cache cotado como banco por vCPU, storage e IOPS pela
razão de storage, cobranças que só existem na AWS levadas ao destino.

Aqui cada linha do balde de banco vira uma parte, pelo tipo de uso (IO antes de
storage: o `Aurora:StorageIOUsage` é requisição de IO, não GB):

- **instância**: vCPU-hora (horas x vCPU da classe; com réplica síncrona, cada réplica
  conta), cotada pela taxa do motor no destino;
- **instância sem horas**: custo que paga a instância sem trazer hora (a taxa da reserva,
  a memória do Cloud SQL), somado ao custo das instâncias;
- **cache**: GB-hora da memória do nó, pela faixa de tamanho do nó;
- **armazenamento** e **backup**: GB-mês;
- **sem equivalente**: IOPS provisionado, throughput, créditos de CPU, extended support,
  requisições de IO — cobranças da origem que o destino não tem;
- **outros**: o que não se encaixa (proxy, serverless do Aurora), ao custo da origem.

Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domain.services.instance_shape import parse_instance_shape
from app.domain.services.memoria_de_cache import MEMORIA_DO_NO_GIB

CATEGORIA = "database"
UNIDADE = "vcpu_hour"
MYSQL = "mysql"
POSTGRESQL = "postgresql"
# Categoria de taxa de cada motor; sem motor conhecido, a de `database` (a maior)
CATEGORIA_DO_MOTOR = {MYSQL: "database_mysql", POSTGRESQL: "database_postgresql"}
CATEGORIA_STORAGE = "database_storage"
CATEGORIA_BACKUP = "database_backup"
CATEGORIA_STORAGE_DO_MOTOR = {m: f"{CATEGORIA_STORAGE}_{m}" for m in (MYSQL, POSTGRESQL)}
CATEGORIA_BACKUP_DO_MOTOR = {m: f"{CATEGORIA_BACKUP}_{m}" for m in (MYSQL, POSTGRESQL)}

INSTANCIA = "instancia"
INSTANCIA_SEM_HORAS = "instancia_sem_horas"
CACHE = "cache"
ARMAZENAMENTO = "armazenamento"
BACKUP = "backup"
SEM_EQUIVALENTE = "sem_equivalente"
OUTROS = "outros"

# Faixa de tamanho do nó de cache: as nuvens cobram o GB diferente conforme o nó (o
# Memorystore tem M1 até 4 GiB, M2 até 10; a OCI Cache, até 10 GB e acima)
ATE_4GB = "ate_4gb"
DE_4_A_10GB = "de_4_a_10gb"
ACIMA_10GB = "acima_10gb"
CATEGORIA_DA_FAIXA = {f: f"cache_{f}" for f in (ATE_4GB, DE_4_A_10GB, ACIMA_10GB)}

# Instâncias em alta disponibilidade por nuvem e motor, contra as 2 que a origem conta
# (primária e réplica). OCI MySQL HeatWave: "três instâncias MySQL, cada uma com o
# mesmo storage, CPU e RAM da shape" (docs.oracle.com, High Availability, 17/10/2026).
_REPLICAS_HA = {("oci", MYSQL): 3}
_REPLICAS_PADRAO = 2
# Cópias do armazenamento em alta disponibilidade. OCI PostgreSQL: o storage otimizado é
# compartilhado pelos nós (premissa declarada); OCI MySQL: um volume por instância.
_COPIAS_DO_STORAGE_HA = {("oci", MYSQL): 3, ("oci", POSTGRESQL): 1}

_DOIS = Decimal(2)
_UM = Decimal(1)
_QUATRO = Decimal(4)
_DEZ = Decimal(10)
# AWS: classe do RDS (db.) ou do ElastiCache (cache.) no UsageType de instância
_AWS_INSTANCIA = re.compile(
    r"(?:InstanceUsage|Multi-AZUsage|HeavyUsage|NodeUsage)[^:]*:(db|cache)\.([a-z0-9]+\.[a-z0-9]+)",
    re.IGNORECASE,
)
# A AWS abrevia tamanhos grandes na fatura: `db.r8g.12xl`
_XL_ABREVIADO = re.compile(r"(\d*)xl$")
# Cobrança da origem sem par no destino; pelo nome do uso ou pela unidade
_SEM_EQUIVALENTE_NO_USO = re.compile(
    r"extendedsupport|cpucredits|storageiousage|iorequests|piops(?!-storage)|throughput|\biops\b",
    re.IGNORECASE,
)
_UNIDADES_SEM_EQUIVALENTE = {"iops-mo", "ios", "gibps-mo", "mibps-mo", "mbps-mo"}
_UNIDADES_GB_MES = {"gb-mo", "gibibyte month", "gb/month", "1 gb/month", "gigabyte month"}
_UNIDADES_HORA = {"hours", "hour", "hrs", "1 hour", "h"}
_AZURE_VCORE = re.compile(r"\bvcore\b", re.IGNORECASE)
_GCP_VCPU = re.compile(r"cloud sql.*-\s*vcpu", re.IGNORECASE)
_GCP_RAM = re.compile(r"cloud sql.*-\s*ram", re.IGNORECASE)
_GCP_REDIS = re.compile(r"redis.*\b(?:capacity|basic|standard)\b.*\bM(\d)\b", re.IGNORECASE)
_REGIONAL = re.compile(r"\bregional\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParteDoBanco:
    tipo: str
    # vCPU-hora (instância), GB-hora (cache), GB-mês (armazenamento, backup); None sem medida
    quantidade: Decimal | None = None
    motor: str | None = None
    ha: bool = False
    faixa: str | None = None


def parte_do_banco(
    provedor: str,
    servico: str | None,
    uso: str | None,
    unidade: str | None,
    quantidade: Decimal | None,
    *,
    motor_declarado: str | None = None,
    cobranca: str | None = None,
) -> ParteDoBanco:
    """A parte de uma linha do balde de banco (ver o docstring do módulo)."""
    p = provedor.lower()
    texto = uso or ""
    un = (unidade or "").strip().lower()
    qtd = quantidade if quantidade is not None and quantidade > 0 else None
    m = motor(p, servico, uso, motor_declarado)
    if p == "aws":
        return _aws(texto, un, qtd, m, cobranca)
    if p == "azure":
        return _por_nome(texto, un, qtd, m, ha=False, vcpu=_vcpu_azure(texto, un, qtd))
    if p == "gcp":
        return _gcp(texto, un, qtd, m)
    if p == "oci":
        return _oci(texto, qtd, m)
    return ParteDoBanco(OUTROS)


def vcpu_horas(
    provedor: str, uso: str | None, quantidade: Decimal | None, unidade: str | None
) -> Decimal | None:
    """vCPU-hora da linha de instância de banco (não de cache), ou None."""
    parte = parte_do_banco(provedor, None, uso, unidade, quantidade)
    return parte.quantidade if parte.tipo == INSTANCIA else None


def motor(provedor: str, servico: str | None, uso: str | None, declarado: str | None) -> str | None:
    """MySQL, PostgreSQL ou None. O atributo da fatura detalhada vence; sem ele, o nome
    do serviço ou do uso (Azure, GCP e OCI dizem o motor no nome)."""
    texto = " ".join(x for x in (declarado, servico, uso) if x).lower()
    if "postgres" in texto:
        return POSTGRESQL
    if "mysql" in texto or "mariadb" in texto:
        return MYSQL
    return None


# Motor de cache não é banco relacional: fica fora da escolha do equivalente
_CACHE_NO_MOTOR = ("redis", "memcached", "valkey")
_NOME_DO_MOTOR = {
    POSTGRESQL: re.compile(r"postgres", re.IGNORECASE),
    MYSQL: re.compile(r"mysql|mariadb", re.IGNORECASE),
}


def motores_da_conta(
    pesos: Mapping[str, Decimal], medidos: Iterable[str | None]
) -> frozenset[str] | None:
    """Os motores relacionais da conta, ou None quando algum é desconhecido.

    O export detalhado (instância a instância, ``medidos``) vence; sem ele, o peso por
    motor que a agregação mediu na fatura, e só quando todo ele tem motor (a chave vazia
    é o banco sem motor conhecido)."""
    relacionais = [m for m in medidos if not any(c in (m or "").lower() for c in _CACHE_NO_MOTOR)]
    if relacionais:
        motores = {motor("", None, None, m) for m in relacionais}
        return None if None in motores else frozenset(m for m in motores if m)
    conhecidos = {m for m, peso in pesos.items() if peso > 0}
    if not conhecidos or "" in conhecidos:
        return None
    return frozenset(conhecidos)


def alvos_do_motor(
    alvos: Sequence[Mapping[str, Any]], motores: frozenset[str]
) -> list[Mapping[str, Any]]:
    """Por destino, só os equivalentes dos motores da conta (o RDS PostgreSQL vira o
    PostgreSQL da Azure, não o SQL Database). Destino sem nenhum do motor no nome (o
    Cloud SQL serve aos dois) fica com todos."""
    por_destino: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for alvo in alvos:
        por_destino[str(alvo.get("provider") or "").lower()].append(alvo)
    saida: list[Mapping[str, Any]] = []
    for do_destino in por_destino.values():
        do_motor = [
            a
            for a in do_destino
            if any(_NOME_DO_MOTOR[m].search(str(a.get("service_name") or "")) for m in motores)
        ]
        saida += do_motor or do_destino
    return saida


def fator_ha(provedor: str, motor_: str | None) -> Decimal:
    """Quanto a vCPU-hora em alta disponibilidade da origem (2 instâncias) vale no
    destino: OCI MySQL tem 3 instâncias, as demais 2."""
    return Decimal(_REPLICAS_HA.get((provedor.lower(), motor_ or ""), _REPLICAS_PADRAO)) / _DOIS


def copias_do_storage(provedor: str, motor_: str | None, ha: bool) -> Decimal:
    if not ha:
        return _UM
    chave = (provedor.lower(), motor_ or "")
    return Decimal(_COPIAS_DO_STORAGE_HA.get(chave, _REPLICAS_PADRAO))


# Preço por unidade que uma linha real pode ter (US$): fora disso a quantidade não é a
# da unidade (IO rotulado como GB-mês, byte em vez de GB) e a linha não é medida
_PLAUSIVEL = {
    ARMAZENAMENTO: (Decimal("0.005"), Decimal(3)),
    BACKUP: (Decimal("0.005"), Decimal(3)),
    CACHE: (Decimal("0.001"), Decimal(1)),
}
_SUFIXO_HA = ":ha"


def chave_do_motor(motor_: str | None, *, ha: bool) -> str:
    """Chave da vCPU-hora por motor: `mysql`, `postgresql:ha`, `` (sem motor)."""
    return (motor_ or "") + (_SUFIXO_HA if ha else "")


def ler_chave_do_motor(chave: str) -> tuple[str | None, bool]:
    ha = chave.endswith(_SUFIXO_HA)
    motor_ = chave.removesuffix(_SUFIXO_HA)
    return (motor_ or None), ha


def chave_da_parte(parte: ParteDoBanco) -> str:
    """Chave das partes fora da instância: `cache:ate_4gb`, `armazenamento:mysql:ha`..."""
    if parte.tipo == CACHE:
        return f"{CACHE}:{parte.faixa}"
    if parte.tipo == ARMAZENAMENTO:
        return f"{ARMAZENAMENTO}:{parte.motor or ''}:{'ha' if parte.ha else ''}"
    if parte.tipo == BACKUP:
        return f"{BACKUP}:{parte.motor or ''}"
    return parte.tipo


def ler_chave_da_parte(chave: str) -> tuple[str, str | None, bool, str | None]:
    """(tipo, motor, alta disponibilidade, faixa) de uma chave de `chave_da_parte`."""
    tipo, *resto = chave.split(":")
    if tipo == CACHE:
        return tipo, None, False, resto[0] if resto else None
    if tipo == ARMAZENAMENTO:
        motor_, ha = [*resto, "", ""][:2]
        return tipo, motor_ or None, ha == "ha", None
    if tipo == BACKUP:
        return tipo, (resto[0] if resto else "") or None, False, None
    return tipo, None, False, None


def preco_plausivel(parte: ParteDoBanco, custo: Decimal) -> bool:
    faixa = _PLAUSIVEL.get(parte.tipo)
    if faixa is None or not parte.quantidade:
        return True
    preco = custo / parte.quantidade
    return faixa[0] <= preco <= faixa[1]


def faixa_do_no(memoria_gib: Decimal) -> str:
    if memoria_gib <= _QUATRO:
        return ATE_4GB
    return DE_4_A_10GB if memoria_gib <= _DEZ else ACIMA_10GB


def _aws(
    uso: str, unidade: str, qtd: Decimal | None, m: str | None, cobranca: str | None
) -> ParteDoBanco:
    baixo = uso.lower()
    if "extendedsupport" in baixo:
        return ParteDoBanco(SEM_EQUIVALENTE)
    instancia = _AWS_INSTANCIA.search(uso)
    if instancia is not None:
        tipo, classe = (
            instancia.group(1).lower(),
            _XL_ABREVIADO.sub(r"\1xlarge", instancia.group(2).lower()),
        )
        # `HeavyUsage` é a taxa da reserva em qualquer formato (no Cost Explorer vem sem
        # `charge_kind`): paga a instância, sem hora; o uso coberto traz as horas
        reserva = cobranca == "commitment" or "heavyusage" in baixo
        if tipo == "cache":
            return _cache_aws(f"cache.{classe}", qtd, reserva=reserva)
        if reserva:
            return ParteDoBanco(INSTANCIA_SEM_HORAS, motor=m)
        forma = parse_instance_shape(classe)
        if forma is None or qtd is None:
            return ParteDoBanco(INSTANCIA_SEM_HORAS, motor=m)
        replicas = _DOIS if "multi-az" in baixo else _UM
        return ParteDoBanco(INSTANCIA, qtd * forma.vcpus * replicas, motor=m, ha=replicas > _UM)
    return _por_nome(uso, unidade, qtd, m, ha="multi-az" in baixo, vcpu=None)


def _cache_aws(tipo: str, qtd: Decimal | None, *, reserva: bool) -> ParteDoBanco:
    memoria = MEMORIA_DO_NO_GIB.get(tipo)
    if memoria is None:
        return ParteDoBanco(OUTROS)
    faixa = faixa_do_no(memoria)
    if reserva or qtd is None:
        # Taxa da reserva do nó: custo do cache, sem hora (o uso coberto traz as horas)
        return ParteDoBanco(CACHE, None, faixa=faixa)
    return ParteDoBanco(CACHE, qtd * memoria, faixa=faixa)


def _vcpu_azure(uso: str, unidade: str, qtd: Decimal | None) -> Decimal | None:
    if qtd is not None and _AZURE_VCORE.search(uso) and unidade in _UNIDADES_HORA:
        return qtd
    return None


def _gcp(uso: str, unidade: str, qtd: Decimal | None, m: str | None) -> ParteDoBanco:
    ha = bool(_REGIONAL.search(uso))
    if _GCP_VCPU.search(uso) and unidade in _UNIDADES_HORA and qtd is not None:
        return ParteDoBanco(INSTANCIA, qtd * (_DOIS if ha else _UM), motor=m, ha=ha)
    if _GCP_RAM.search(uso):
        return ParteDoBanco(INSTANCIA_SEM_HORAS, motor=m)
    redis = _GCP_REDIS.search(uso)
    if redis is not None and qtd is not None:
        nivel = int(redis.group(1))
        faixa = ATE_4GB if nivel == 1 else DE_4_A_10GB if nivel == 2 else ACIMA_10GB  # noqa: PLR2004
        # Standard tem a réplica: o GB vale dois nós
        nos = _DOIS if "standard" in uso.lower() else _UM
        return ParteDoBanco(CACHE, qtd * nos, faixa=faixa)
    return _por_nome(uso, unidade, qtd, m, ha=ha, vcpu=None)


def _oci(uso: str, qtd: Decimal | None, m: str | None) -> ParteDoBanco:
    baixo = uso.lower()
    if qtd is not None and "mysql" in baixo and "ecpu" in baixo:
        # ECPU = vCPU: premissa declarada (a Oracle não publica a relação)
        return ParteDoBanco(INSTANCIA, qtd, motor=MYSQL)
    if qtd is not None and "postgresql" in baixo and "ocpu" in baixo:
        return ParteDoBanco(INSTANCIA, qtd * _DOIS, motor=POSTGRESQL)
    if "cache" in baixo and qtd is not None:
        faixa = ACIMA_10GB if "high memory" in baixo else DE_4_A_10GB
        return ParteDoBanco(CACHE, qtd, faixa=faixa)
    if "backup" in baixo and qtd is not None:
        return ParteDoBanco(BACKUP, qtd, motor=m)
    if "storage" in baixo and qtd is not None:
        return ParteDoBanco(ARMAZENAMENTO, qtd, motor=m)
    return ParteDoBanco(OUTROS)


def _por_nome(
    uso: str,
    unidade: str,
    qtd: Decimal | None,
    m: str | None,
    *,
    ha: bool,
    vcpu: Decimal | None,
) -> ParteDoBanco:
    """O que não é instância pelo nome: sem equivalente, backup, armazenamento, outros."""
    if vcpu is not None:
        return ParteDoBanco(INSTANCIA, vcpu, motor=m, ha=ha)
    if unidade in _UNIDADES_SEM_EQUIVALENTE or _SEM_EQUIVALENTE_NO_USO.search(uso):
        return ParteDoBanco(SEM_EQUIVALENTE)
    baixo = uso.lower()
    if "backup" in baixo:
        return ParteDoBanco(BACKUP, qtd, motor=m) if qtd is not None else ParteDoBanco(OUTROS)
    # Sem unidade (o Cost Explorer não traz), o nome manda; a trava de preço plausível
    # do agregador barra a quantidade que não é GB
    if "storage" in baixo and (unidade in _UNIDADES_GB_MES or not unidade) and qtd is not None:
        return ParteDoBanco(ARMAZENAMENTO, qtd, motor=m, ha=ha)
    return ParteDoBanco(OUTROS)
