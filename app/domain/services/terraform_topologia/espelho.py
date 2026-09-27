"""Subredes espelhadas e VMs repartidas por subrede (RI15.1 a RI15.3 e RI15.5).

Só subrede com recurso posicionado é espelhada: VM de pool que virou recurso e,
na AWS, banco gerado (é a única nuvem em que o pacote põe o banco na rede; nas
outras isso exige endpoint privado, decisão de rede do cliente).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.domain.services.terraform_plan import (
    Nomeador,
    PlanoDerivado,
    Premissa,
    RecursoDerivado,
)
from app.domain.services.terraform_topologia.posicionamento import (
    SEM_POSICIONAMENTO,
    Fatia,
    fatias_do_pool,
    repartir,
)
from app.domain.services.terraform_topologia.rede import (
    Alocacao,
    SubredeDeOrigem,
    alocar,
    de_payload,
    sobreposicoes,
)

SUBNET_PADRAO = "subnet_principal"
_MAX_LISTADOS = 5
# O RDS exige subredes em duas zonas para um grupo de subredes.
_ZONAS_DO_RDS = 2


def espelhar(
    plano: PlanoDerivado,
    detalhe: Mapping[str, Any],
    *,
    destino: str,
    rede: str,
    acesso_publico: bool,
    redes_de_origem: int = 0,
) -> None:
    rede_origem = detalhe.get("rede") or {}
    por_pool = {(p.get("familia"), p.get("regiao")): p for p in detalhe.get("posicionamento") or []}
    fatias: dict[str, list[Fatia]] = {}
    for r in plano.recursos:
        cfg = r.configuration
        if r.resource_type != "compute" or cfg.get("disk_only"):
            continue
        pos = por_pool.get((cfg.get("source_family"), cfg.get("source_region")))
        if pos is not None:
            fatias[r.logical_name] = repartir(
                _inteiro(cfg.get("instances"), 1), fatias_do_pool(pos)
            )

    necessarias = {f.subrede for fs in fatias.values() for f in fs if f.vms and f.subrede}
    if destino == "aws":
        necessarias |= _subredes_dos_bancos(plano, detalhe)
    todas = {s.chave: s for s in de_payload(rede_origem.get("subredes") or [])}
    origens = [s for chave, s in todas.items() if chave in necessarias]
    alocacao = alocar(rede, origens)
    plano.avisos.extend(alocacao.avisos)
    nomes = _criar_subredes(plano, alocacao, acesso_publico)
    _fatiar_pools(plano, fatias, nomes, todas)
    _ligar_bancos(plano, nomes, alocacao, destino)
    if origens:
        # Sem subrede de origem para espelhar, a rede continua a de antes.
        _premissa_da_rede(plano, alocacao, rede_origem, rede, redes_de_origem)
    _avisos_de_exposicao(plano, alocacao, acesso_publico, destino)


def _inteiro(valor: object, padrao: int) -> int:
    return int(valor) if isinstance(valor, int | float) and not isinstance(valor, bool) else padrao


def _lista(valor: object) -> list[str]:
    return [str(x) for x in valor] if isinstance(valor, list | tuple) else []


def _subredes_dos_bancos(plano: PlanoDerivado, detalhe: Mapping[str, Any]) -> set[str]:
    por_grupo = {
        (b.get("classe"), b.get("motor"), bool(b.get("multi_az")), b.get("regiao")): b
        for b in detalhe.get("bancos") or []
    }
    saida: set[str] = set()
    for i, r in enumerate(plano.recursos):
        cfg = r.configuration
        if r.resource_type != "managed_database" or not cfg.get("source_class"):
            continue
        subredes = _lista(cfg.get("source_subnets"))
        if not subredes:
            grupo = por_grupo.get(
                (
                    cfg.get("source_class"),
                    cfg.get("engine_measured"),
                    bool(cfg.get("source_multi_az")),
                    cfg.get("source_region"),
                )
            )
            subredes = list((grupo or {}).get("subredes") or [])
            plano.recursos[i] = replace(r, configuration={**cfg, "source_subnets": subredes})
        saida |= set(subredes)
    return saida


def _criar_subredes(
    plano: PlanoDerivado, alocacao: Alocacao, acesso_publico: bool
) -> dict[str, str]:
    """Chave de origem → nome lógico da subrede de destino."""
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    nomes: dict[str, str] = {}
    novas = []
    for s in alocacao.subredes:
        nome = nomeador.gerar("subnet", s.origem.nome or s.origem.chave.rsplit("/", 1)[-1])
        nomes[s.origem.chave] = nome
        novas.append(
            RecursoDerivado(
                logical_name=nome,
                resource_type="subnet",
                configuration={
                    "cidr": s.cidr,
                    "public_access": s.origem.publica and acesso_publico,
                    "zone_index": s.zona,
                    "mirror_of": {
                        "chave": s.origem.chave,
                        "nome": s.origem.nome,
                        "cidr": s.origem.cidr,
                    },
                },
                depends_on=("rede_principal",),
            )
        )
    # As subredes vêm antes de tudo que as referencia.
    plano.recursos[:0] = novas
    return nomes


def _fatiar_pools(
    plano: PlanoDerivado,
    fatias: Mapping[str, list[Fatia]],
    nomes: Mapping[str, str],
    origem: Mapping[str, SubredeDeOrigem],
) -> None:
    """Um recurso por (pool, subrede de destino). Fatias que caem na mesma subrede
    (a padrão, quando a alocação não coube) viram um recurso só."""
    # IP público só onde a subrede de DESTINO é pública (origem pública e a
    # requisição pediu acesso público). VM pedindo IP em subrede que o proíbe
    # falha no apply da OCI.
    publica = {
        r.logical_name: bool(r.configuration.get("public_access"))
        for r in plano.recursos
        if r.resource_type == "subnet"
    }
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    saida: list[RecursoDerivado] = []
    for r in plano.recursos:
        partes = fatias.get(r.logical_name)
        if partes is None:
            saida.append(r)
            continue
        por_destino: dict[str, list[Fatia]] = {}
        for f in partes:
            if f.vms:
                por_destino.setdefault(nomes.get(f.subrede, SUBNET_PADRAO), []).append(f)
        linhas = []
        for subrede, juntas in por_destino.items():
            rotulo = subrede.removeprefix("subnet_") if subrede != SUBNET_PADRAO else "padrao"
            saida.append(
                replace(
                    r,
                    logical_name=nomeador.gerar(r.logical_name, rotulo),
                    configuration={
                        **r.configuration,
                        **_juntar(juntas),
                        "subnet": subrede,
                        "public_ip": publica.get(subrede, False),
                        "pool": r.logical_name,
                    },
                    depends_on=(subrede,),
                )
            )
            vms = sum(f.vms for f in juntas)
            de_origem = ", ".join(_rotulo(f.subrede, origem) for f in juntas)
            linhas.append(f"{vms} em {subrede} (origem: {de_origem})")
        sem_vm = [f for f in partes if not f.vms]
        _premissa_do_pool(
            plano, r, linhas=linhas, sem_vm=sem_vm, subredes=len(partes), origem=origem
        )
    plano.recursos[:] = saida


def _juntar(fatias: list[Fatia]) -> dict[str, Any]:
    vms = sum(f.vms for f in fatias)
    com_boot = [(f.disco_boot_gb, f.instancias_origem) for f in fatias if f.disco_boot_gb]
    pesos = sum(n for _, n in com_boot)
    boot = (
        -(-sum(gb * n for gb, n in com_boot) // pesos) if pesos else None  # média, para cima
    )
    return {
        "instances": vms,
        "source_subnets": sorted(f.subrede for f in fatias if f.subrede),
        "placement_groups": sorted({g for f in fatias for g in f.grupos}),
        "placement_group_sets": max(f.conjuntos_de_grupos for f in fatias),
        "slice_boot_gb": boot,
    }


def _rotulo(chave: str, origem: Mapping[str, SubredeDeOrigem]) -> str:
    if chave == SEM_POSICIONAMENTO:
        return "sem posicionamento"
    s = origem.get(chave)
    if s is None:
        return chave.rsplit("/", 1)[-1]
    return f"{s.nome or chave.rsplit('/', 1)[-1]} {s.cidr or ''}".strip()


def _premissa_do_pool(
    plano: PlanoDerivado,
    r: RecursoDerivado,
    *,
    linhas: list[str],
    sem_vm: list[Fatia],
    subredes: int,
    origem: Mapping[str, SubredeDeOrigem],
) -> None:
    total = _inteiro(r.configuration.get("instances"), 1)
    base = (
        f"As {total} VMs aprovadas foram repartidas pelo vCPU de cada subrede de origem, "
        "pelo maior resto, com ao menos uma por subrede quando cabe: " + "; ".join(linhas) + "."
    )
    plano.premissas.append(
        Premissa(
            campo=f"{r.logical_name}.posicionamento",
            categoria="derivacao",
            valor=f"{len(linhas)} subrede(s)",
            base=base[:800],
        )
    )
    if sem_vm:
        plano.avisos.append(
            f"Pool '{r.configuration.get('source_family')}': {total} VM(s) aprovada(s) para "
            f"{subredes} subredes de origem. Ficaram sem máquina: "
            + ", ".join(_rotulo(f.subrede, origem) for f in sem_vm[:_MAX_LISTADOS])
            + ("…" if len(sem_vm) > _MAX_LISTADOS else "")
            + "."
        )


def _ligar_bancos(
    plano: PlanoDerivado, nomes: Mapping[str, str], alocacao: Alocacao, destino: str
) -> None:
    """Na AWS, o banco vai para um grupo de subredes da VPC nova (RI15.6).

    O RDS exige duas zonas. Quando as subredes do banco ficam numa só (sempre, com
    origem regional), o grupo é completado com subredes espelhadas de outra zona:
    sem grupo, o RDS cairia na VPC padrão da conta, onde os grupos de segurança da
    VPC nova não se aplicam.
    """
    bancos = [(i, r) for i, r in enumerate(plano.recursos) if r.resource_type == "managed_database"]
    if not bancos:
        return
    if destino != "aws":
        plano.avisos.append(
            f"Bancos em {destino.upper()} ficam fora das subredes espelhadas: pôr o banco na "
            "rede exige endpoint privado, decisão de rede sua."
        )
        return
    zona_de = {nomes[s.origem.chave]: s.zona for s in alocacao.subredes}
    for i, r in bancos:
        proprias = sorted(
            {nomes[s] for s in _lista(r.configuration.get("source_subnets")) if s in nomes}
        )
        if not proprias:
            continue
        grupo = list(proprias)
        zonas = {zona_de[n] for n in grupo}
        completadas = []
        for nome, zona in zona_de.items():  # ordem de alocação: determinística
            if len(zonas) >= _ZONAS_DO_RDS:
                break
            if zona not in zonas:
                grupo.append(nome)
                zonas.add(zona)
                completadas.append(nome)
        if len(zonas) < _ZONAS_DO_RDS:
            plano.avisos.append(
                f"Banco {r.logical_name}: não há subrede espelhada em uma segunda zona, e o RDS "
                "exige duas. Ficou fora da VPC nova, sem os grupos de segurança espelhados."
            )
            continue
        plano.recursos[i] = replace(r, configuration={**r.configuration, "db_subnets": grupo})
        if completadas:
            plano.premissas.append(
                Premissa(
                    campo=f"{r.logical_name}.db_subnets",
                    categoria="estimativa",
                    valor=", ".join(grupo),
                    base=(
                        f"Na origem o banco estava em {', '.join(proprias)}, numa zona só. O RDS "
                        "exige duas: o grupo foi completado com "
                        + ", ".join(completadas)
                        + ", de outra zona. Escolha do pacote, não fato medido."
                    ),
                )
            )


def _premissa_da_rede(
    plano: PlanoDerivado,
    alocacao: Alocacao,
    rede_origem: Mapping[str, Any],
    rede: str,
    redes_de_origem: int,
) -> None:
    """``redes_de_origem`` vem da contagem do inventário: no GCP a VPC não tem
    CIDR, e contar pelos CIDRs perderia as redes de cada projeto."""
    plano.premissas = [p for p in plano.premissas if p.campo != "rede"]
    redes = list(rede_origem.get("redes_origem") or [])
    quantas = max(redes_de_origem, len(redes))
    base = (
        f"A faixa {rede} vem da requisição. {len(alocacao.subredes)} subrede(s) espelham as de "
        f"origem com recurso posicionado, com endereçamento novo dentro dela (estratégia: "
        f"{alocacao.estrategia}): reusar os CIDRs da origem quebraria a VPN da migração. "
        "A subrede padrão (primeira /24) recebe o que não tem posicionamento."
    )
    if quantas > 1:
        base += (
            f" As {quantas} redes de origem viram uma: o isolamento entre elas passa a ser "
            "dos grupos de segurança."
        )
    plano.premissas.append(
        Premissa(campo="rede", categoria="derivacao", valor=rede, base=base[:800])
    )
    sobrepostas = sobreposicoes(rede, [*redes, *(rede_origem.get("subredes_origem") or [])])
    if sobrepostas:
        plano.avisos.append(
            f"A faixa pedida ({rede}) sobrepõe a origem ("
            + ", ".join(sobrepostas[:_MAX_LISTADOS])
            + "): com as duas nuvens ligadas pela VPN durante a migração, faixas sobrepostas "
            "não roteiam. Escolha outra faixa."
        )


def _avisos_de_exposicao(
    plano: PlanoDerivado, alocacao: Alocacao, acesso_publico: bool, destino: str
) -> None:
    publicas = [s for s in alocacao.subredes if s.origem.publica]
    if publicas and not acesso_publico:
        plano.avisos.append(
            f"{len(publicas)} subrede(s) eram públicas na origem ("
            + ", ".join(str(s.origem.nome or s.origem.chave) for s in publicas[:_MAX_LISTADOS])
            + "); saíram privadas porque a requisição não pediu acesso público."
        )
    elif publicas and destino == "azure":
        plano.avisos.append(
            "Na Azure o pacote não cria IP público para as VMs das subredes que eram públicas "
            "na origem: associe um `azurerm_public_ip` à NIC de quem precisa."
        )
    juntas = [
        (r.logical_name, n)
        for r in plano.recursos
        if (n := _inteiro(r.configuration.get("placement_group_sets"), 1)) > 1
    ]
    if juntas:
        plano.avisos.append(
            f"{len(juntas)} VM(s) consolidam instâncias com grupos de segurança diferentes e "
            "recebem a união deles (ex.: "
            + ", ".join(f"{n} com {k} combinações" for n, k in juntas[:_MAX_LISTADOS])
            + "). Confirme que a aplicação pode aceitar o que cada uma aceitava."
        )
