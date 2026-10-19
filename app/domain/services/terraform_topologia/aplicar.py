"""Aplica a topologia do inventário ao plano derivado da análise (RI15).

Roda depois de ``derivar_plano`` e só quando a análise congelou uma
``topologia``. Sem ela, o plano não passa por aqui, e o pacote sai byte a byte
igual ao de antes (teste de caracterização).

O plano de entrada não é alterado: sai um plano novo.
"""

from __future__ import annotations

import ipaddress
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

from app.domain.services.terraform_plan import (
    Nomeador,
    PlanoDerivado,
    Premissa,
    RecursoDerivado,
    familia_do_motor,
    motor_aceito,
)
from app.domain.services.terraform_topologia.bancos import (
    agrupar_sem_grupo,
    versao_padrao,
)
from app.domain.services.terraform_topologia.discos import Avulsos, avulsos, boot_medio
from app.domain.services.terraform_topologia.espelho import espelhar
from app.domain.services.terraform_topologia.grupos import espelhar_grupos

# Chave do payload de cada categoria de disco.
_CHAVES_DE_DISCO = {"storage_ssd": "armazenamento_ssd", "storage_hdd": "armazenamento_hdd"}
# Teto de um volume no plano (o do GCP); cada renderer aplica o do seu destino.
_GB_MAXIMO = 65536


def aplicar_topologia(
    plano: PlanoDerivado,
    payload: Mapping[str, Any],
    *,
    destino: str,
    rede: str,
    acesso_publico: bool,
) -> PlanoDerivado:
    topologia = payload.get("topologia") or {}
    detalhe = topologia.get("detalhe") or {}
    novo = PlanoDerivado(
        recursos=list(plano.recursos),
        premissas=list(plano.premissas),
        nao_suportado=list(plano.nao_suportado),
        avisos=list(plano.avisos),
        variaveis_padrao=dict(plano.variaveis_padrao),
    )
    # O banco sem classe entra antes do espelho: as subredes dele também contam.
    _bancos_sem_grupo(novo, detalhe.get("bancos_sem_grupo") or [], destino)
    fundacao = ((topologia.get("resumo") or {}).get("contagens") or {}).get("fundacao") or {}
    espelhar(
        novo,
        detalhe,
        destino=destino,
        rede=rede,
        acesso_publico=acesso_publico,
        redes_de_origem=int(fundacao.get("rede") or 0),
    )
    espelhar_grupos(
        novo,
        detalhe,
        destino=destino,
        rede=rede,
        provedor_origem=str(payload.get("provedor_origem") or "").lower(),
    )
    _disco_raiz(novo, detalhe.get("posicionamento") or [], detalhe.get("discos") or {})
    medido = (payload.get("inventario_medido") or {}).get("armazenamento") or {}
    _discos_avulsos(novo, medido, detalhe.get("discos") or {})
    _versoes(novo, detalhe.get("bancos") or [], destino)
    _fronteira_do_escopo(novo, topologia.get("escopo") or {}, destino=destino, rede=rede)
    _saida_da_rede(novo, destino, rede)
    _buckets_do_escopo(novo, topologia.get("escopo") or {})
    _kubernetes_do_escopo(novo, topologia.get("escopo") or {})
    _componentes_do_escopo(novo, topologia.get("escopo") or {}, destino)
    return novo


# A AWS só põe NAT em subrede pública: uma /28 própria, livre no fim da faixa da rede
_PREFIXO_DA_SUBREDE_DO_NAT = 28
# A subrede principal é a primeira /24 da rede (build_blueprint)
_PREFIXO_DA_PRINCIPAL = 24


def _saida_da_rede(plano: PlanoDerivado, destino: str, rede: str) -> None:
    """Gateway de internet e NAT para a rede espelhada (item 12).

    Só quando há subrede espelhada da origem: é o pacote com topologia, e a origem tem
    a saída. A subrede privada sai pela NAT e a pública pelo gateway de internet; quais
    são, o renderer lê das subredes do pacote."""
    if not any(
        r.resource_type == "subnet" and r.configuration.get("mirror_of") for r in plano.recursos
    ):
        return
    configuracao: dict[str, Any] = {}
    if destino == "aws":
        livre = _faixa_livre(plano, rede, _PREFIXO_DA_SUBREDE_DO_NAT)
        if livre is None:
            plano.avisos.append(
                "Sem faixa livre na rede para a subrede do NAT: as subredes privadas ficam "
                "sem saída para a internet. Aumente a faixa da rede ou crie o NAT à parte."
            )
        else:
            nome = Nomeador(r.logical_name for r in plano.recursos).gerar("subnet", "saida_nat")
            plano.recursos.insert(
                0,
                RecursoDerivado(
                    logical_name=nome,
                    resource_type="subnet",
                    configuration={"cidr": livre, "public_access": True, "nat_only": True},
                    depends_on=("rede_principal",),
                ),
            )
            configuracao["nat_subnet"] = nome
    plano.recursos.append(
        RecursoDerivado(
            logical_name="saida_da_rede",
            resource_type="network_egress",
            configuration=configuracao,
            depends_on=("rede_principal",),
        )
    )


def _faixa_livre(plano: PlanoDerivado, rede: str, prefixo: int) -> str | None:
    """A última faixa do tamanho pedido que não cruza nenhuma subrede do pacote (a
    principal é a primeira /24 da rede)."""
    try:
        cidr = ipaddress.IPv4Network(rede)
    except ValueError:
        return None
    usadas = (
        [next(cidr.subnets(new_prefix=_PREFIXO_DA_PRINCIPAL), cidr)]
        if cidr.prefixlen < _PREFIXO_DA_PRINCIPAL
        else [cidr]
    )
    for r in plano.recursos:
        if r.resource_type == "subnet" and r.configuration.get("cidr"):
            try:
                usadas.append(ipaddress.IPv4Network(str(r.configuration["cidr"])))
            except ValueError:
                continue
    if cidr.prefixlen >= prefixo:
        return None
    for candidata in reversed(list(cidr.subnets(new_prefix=prefixo))):
        if not any(candidata.overlaps(u) for u in usadas):
            return str(candidata)
    return None


def _pools_da_familia(plano: PlanoDerivado, familia: str) -> list[RecursoDerivado]:
    return [
        r
        for r in plano.recursos
        if r.resource_type == "compute"
        and not r.configuration.get("disk_only")
        and str(r.configuration.get("source_family") or "").upper() == familia
    ]


def _kubernetes_do_escopo(plano: PlanoDerivado, escopo: Mapping[str, Any]) -> None:
    """O cluster do escopo com os grupos de nós dele (itens 15 e 19).

    Os pools de máquinas que são nós do cluster saem da lista de VMs e entram no grupo
    de nós, com a máquina e a quantidade que a análise cotou. Pool só em parte no
    cluster é dividido pela contagem de nós da família no escopo."""
    clusters = escopo.get("clusters") or []
    if not clusters:
        return
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    gerados = 0
    for cluster in clusters:
        nome_origem = str(cluster.get("nome") or "cluster")
        grupos = []
        for g in cluster.get("grupos") or []:
            familia = str(g.get("familia") or "").upper()
            pools = _pools_da_familia(plano, familia)
            if not pools:
                continue
            # O espelho pode repartir o pool pelas subredes: os nós saem dos pedaços em ordem
            total = sum(int(str(p.configuration.get("instances") or 1)) for p in pools)
            da_familia = max(int(g.get("da_familia") or 0), int(g.get("maquinas") or 0), 1)
            nos = min(total, max(1, math.ceil(total * int(g.get("maquinas") or 0) / da_familia)))
            primeiro = pools[0].configuration
            grupos.append(
                {
                    "name": str(g.get("nome") or familia.lower()),
                    "machine_type": primeiro.get("machine_type"),
                    "instances": nos,
                    "vcpus": _parte(primeiro.get("vcpus"), nos, _instancias(primeiro)),
                    "ram_gb": _parte(primeiro.get("ram_gb"), nos, _instancias(primeiro)),
                    "source_family": familia,
                    **({"subnet": primeiro["subnet"]} if primeiro.get("subnet") else {}),
                    # Os grupos espelhados do pool: a regra "a partir do grupo dos nós" casa
                    **(
                        {"security_groups": list(primeiro["security_groups"])}
                        if primeiro.get("security_groups")
                        else {}
                    ),
                }
            )
            falta = nos
            for pool in pools:
                cfg = pool.configuration
                n = _instancias(cfg)
                tirar = min(falta, n)
                falta -= tirar
                plano.recursos.remove(pool)
                if n - tirar > 0:
                    plano.recursos.append(
                        replace(
                            pool,
                            configuration={
                                **cfg,
                                "instances": n - tirar,
                                "vcpus": _parte(cfg.get("vcpus"), n - tirar, n),
                                "ram_gb": _parte(cfg.get("ram_gb"), n - tirar, n),
                            },
                        )
                    )
        if not grupos:
            plano.avisos.append(
                f"Cluster {nome_origem}: nenhum nó dele no escopo (o serviço das máquinas não "
                "foi escolhido); o pacote não cria cluster sem nós."
            )
            continue
        nome = nomeador.gerar("cluster", nome_origem)
        plano.recursos.append(
            RecursoDerivado(
                logical_name=nome,
                resource_type="kubernetes_cluster",
                configuration={
                    "source_name": nome_origem,
                    "source_version": cluster.get("versao"),
                    "node_pools": grupos,
                    "service_balancers": list(cluster.get("balanceadores") or []),
                },
                depends_on=("rede_principal",),
            )
        )
        plano.premissas.append(
            Premissa(
                campo=f"{nome}.node_pools",
                categoria="derivacao",
                valor=", ".join(
                    f"{g['name']} {g['machine_type']} x{g['instances']}" for g in grupos
                ),
                base=(
                    f"Nós do cluster {nome_origem} no inventário, pelo grupo de nós; a máquina "
                    "e a quantidade são as que a análise cotou para o pool da família."
                ),
            )
        )
        balanceadores = cluster.get("balanceadores") or []
        plano.avisos.append(
            f"Cluster {nome_origem}: o pacote cria o cluster e os grupos de nós; as aplicações "
            "de dentro dele (Deployment, Service, Ingress, Helm) não viajam pelo Terraform: "
            "reaplique os manifests no destino."
            + (
                f" O balanceador {', '.join(balanceadores)} é de um Service do cluster e nasce "
                "dele no destino; o pacote não o cria à parte."
                if balanceadores
                else ""
            )
        )
        gerados += 1
    if gerados:
        # O cluster agora vai no pacote: sai a frase do plano de controle fora do MVP
        plano.nao_suportado = [x for x in plano.nao_suportado if not x.startswith("Kubernetes:")]


def _instancias(cfg: Mapping[str, Any]) -> int:
    return max(1, int(str(cfg.get("instances") or 1)))


def _parte(valor: object, parte: int, total: int) -> float | None:
    numero = _decimal_ou_none(valor)
    if numero is None or total <= 0:
        return None
    return float(round(numero * parte / total, 2))


def _decimal_ou_none(valor: object) -> Decimal | None:
    try:
        return Decimal(str(valor)) if valor is not None else None
    except ArithmeticError:
        return None


# Registro e segredo viram um recurso por item onde o destino tem o objeto, e um
# contêiner com a lista onde ele não tem: o registro da Azure guarda os repositórios, o
# cofre da Azure e da OCI exige o valor do segredo na criação
_UM_SO = {("registro_de_imagens", "azure"), ("segredo", "azure"), ("segredo", "oci")}
_TIPO_DO_COMPONENTE = {
    "registro_de_imagens": "container_registry",
    "segredo": "vault",
    "zona_dns": "dns_zone",
}


def _componentes_do_escopo(plano: PlanoDerivado, escopo: Mapping[str, Any], destino: str) -> None:
    componentes = escopo.get("componentes") or []
    if not componentes:
        return
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    por_tipo: dict[str, list[str]] = defaultdict(list)
    for c in componentes:
        if str(c.get("tipo")) in _TIPO_DO_COMPONENTE:
            por_tipo[str(c["tipo"])].append(str(c.get("nome") or ""))
    for tipo, nomes in sorted(por_tipo.items()):
        recurso = _TIPO_DO_COMPONENTE[tipo]
        if (tipo, destino) in _UM_SO:
            plano.recursos.append(
                RecursoDerivado(
                    logical_name=nomeador.gerar(recurso, "escopo"),
                    resource_type=recurso,
                    configuration={"items": sorted(nomes)},
                )
            )
            continue
        for origem in sorted(nomes):
            plano.recursos.append(
                RecursoDerivado(
                    logical_name=nomeador.gerar(recurso, origem),
                    resource_type=recurso,
                    configuration={"source_name": origem},
                )
            )
    if por_tipo.get("segredo"):
        plano.avisos.append(
            f"{len(por_tipo['segredo'])} segredo(s) da origem: o pacote cria "
            + (
                "o cofre; crie cada segredo nele com o valor"
                if ("segredo", destino) in _UM_SO
                else "cada segredo vazio, com o nome de origem; preencha o valor"
            )
            + " no destino. O valor de um segredo nunca viaja pelo pacote."
        )
    if por_tipo.get("registro_de_imagens"):
        plano.avisos.append(
            "Registro de imagens: o pacote cria o repositório; as imagens não viajam pelo "
            "Terraform. Copie-as da origem (skopeo copy, crane copy) antes de subir o cluster."
        )
    if por_tipo.get("zona_dns"):
        plano.avisos.append(
            "Zona de DNS: o pacote cria a zona, privada e ligada à rede do pacote; os "
            "registros não viajam. Exporte-os da origem e importe no destino."
        )


def _buckets_do_escopo(plano: PlanoDerivado, escopo: Mapping[str, Any]) -> None:
    """Um bucket por bucket do inventário no escopo, no lugar do agregado pelo custo.

    O agregado só existia quando a fatura ligava custo aos buckets; sem isso o escopo
    ficava sem bucket nenhum, e calado. O tamanho de referência sai do custo ligado a
    cada um, pela mesma taxa da origem que dimensionava o agregado.
    """
    buckets = escopo.get("buckets") or []
    if not buckets:
        return
    agregados = {r.logical_name for r in plano.recursos if r.resource_type == "object_storage"}
    base = next((r.configuration for r in plano.recursos if r.logical_name in agregados), {})
    plano.recursos = [r for r in plano.recursos if r.logical_name not in agregados]
    plano.premissas = [p for p in plano.premissas if p.campo.split(".")[0] not in agregados]
    custo_total = sum(Decimal(str(b.get("custo") or 0)) for b in buckets)
    gb_total = Decimal(str(base.get("estimated_gb") or 0))
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    for b in buckets:
        origem = str(b.get("nome") or "")
        nome = nomeador.gerar("bucket", origem)
        custo = Decimal(str(b.get("custo") or 0))
        # O agregado já dividiu custo por taxa; cada bucket fica com a parte do custo dele
        gb = int(gb_total * custo / custo_total) if custo > 0 and custo_total > 0 else 0
        plano.recursos.append(
            RecursoDerivado(
                logical_name=nome,
                resource_type="object_storage",
                configuration={
                    **({"location": base["location"]} if base.get("location") else {}),
                    "storage_class": "STANDARD",
                    "source_name": origem,
                    **(
                        {"estimated_gb": gb, "size_source": base.get("size_source") or "estimado"}
                        if gb > 0
                        else {"size_source": "sem_custo"}
                    ),
                },
            )
        )
        plano.premissas.append(
            Premissa(
                campo=f"{nome}.origem",
                categoria="derivacao",
                valor=origem,
                base=(
                    "Bucket do inventário, no escopo. "
                    + (
                        f"Volume de referência: ~{gb} GB, a parte do custo ligada a ele."
                        if gb > 0
                        else "A fatura não liga custo a este bucket: o volume não é conhecido."
                    )
                ),
            )
        )


# Ligações da fronteira listadas uma a uma nos avisos; o resto, no README
_MAX_NA_FRONTEIRA = 8


def _fronteira_do_escopo(
    plano: PlanoDerivado, escopo: Mapping[str, Any], *, destino: str, rede: str
) -> None:
    """Só o serviço: o que o recorte cortou abre os avisos da resposta, além do README.
    Vem primeiro porque é o que quebra a aplicação sem conexão entre as nuvens."""
    if not escopo.get("somente_os_servicos"):
        return
    fronteira = list(escopo.get("fronteira") or [])
    fortes = sum(1 for f in fronteira if f.get("forte"))
    # Só a AWS anexa ao banco o grupo de segurança espelhado (`grupos._anexos`): nas outras
    # nuvens o banco fica na subrede de dados, sem a regra de entrada de quem ficou
    tem_banco = any(r.resource_type == "managed_database" for r in plano.recursos)
    portas_no_banco = sorted(
        {
            p
            for f in fronteira
            if f.get("sentido") == "entra" and f.get("natureza") == "dados"
            for p in f.get("portas") or []
        }
    )
    sai = [f for f in fronteira if f.get("sentido") == "sai"]
    if not fronteira:
        novos = [
            "Só os serviços escolhidos: nenhuma dependência de rede atravessa a fronteira "
            "pelas regras do inventário."
        ]
    else:
        novos = [
            f"Só os serviços escolhidos: {len(fronteira)} dependência(s) atravessam a fronteira "
            f"({fortes} forte(s), de dados ou encaminhamento). Sem conexão privada entre as "
            "nuvens elas quebram; cada uma está abaixo e no README.",
            *(str(f.get("texto")) for f in fronteira[:_MAX_NA_FRONTEIRA]),
        ]
        if len(fronteira) > _MAX_NA_FRONTEIRA:
            novos.append(
                f"E mais {len(fronteira) - _MAX_NA_FRONTEIRA} na seção de escopo do README."
            )
        aviso_do_banco = _aviso_da_entrada_do_banco(plano, destino, portas_no_banco)
        if tem_banco and portas_no_banco and aviso_do_banco:
            novos.insert(1, aviso_do_banco)
        if sai:
            novos.insert(
                1,
                f"O que migra chama {len({f.get('para') for f in sai})} serviço(s) que ficam "
                f"na origem: libere neles a entrada a partir da rede do destino ({rede}).",
            )
    plano.avisos[:0] = novos
    plano.nao_suportado += [str(a) for a in escopo.get("avisos") or []]


def _aviso_da_entrada_do_banco(
    plano: PlanoDerivado, destino: str, portas: list[str]
) -> str | None:
    """Como quem ficou na origem chega ao banco no destino (item 13)."""
    lista = ", ".join(portas)
    if destino == "oci":
        sem_grupo = [
            r.configuration.get("engine_family")
            for r in plano.recursos
            if r.resource_type == "managed_database" and not r.configuration.get("security_groups")
        ]
        if not sem_grupo:
            return None
        motivo = (
            "o MySQL HeatWave não aceita NSG"
            if "mysql" in sem_grupo
            else "o banco não tinha grupo de segurança na origem"
        )
        return (
            f"Na OCI {motivo}: libere {lista} a partir da rede de origem na lista de "
            "segurança da subrede de dados, ou quem ficou na origem não alcança o banco."
        )
    if destino == "azure":
        return (
            f"Na Azure o banco aceita {lista} de toda a rede virtual (VirtualNetwork), que "
            "inclui a rede de origem ligada por VPN: falta só a VPN entre as nuvens."
        )
    if destino == "gcp":
        return (
            "No GCP o Cloud SQL fica na rede do serviço (acesso privado); o pacote exporta as "
            f"rotas da VPC para esse peering, então {lista} chega da origem pela VPN entre as "
            "nuvens."
        )
    return None


# ── disco raiz ──────────────────────────────────────────────────────────


def _disco_raiz(
    plano: PlanoDerivado, posicionamento: list[Mapping[str, Any]], discos: Mapping[str, Any]
) -> None:
    """O boot medido vira o disco raiz da VM (RI15.7).

    Tamanho: o boot médio da subrede de origem da VM; sem ele, o do pool. Classe:
    a que domina o boot da origem, em GB (HDD só quando o boot era
    majoritariamente magnético).
    """
    boot = discos.get("boot") or {}
    hdd = (boot.get("armazenamento_hdd") or {}).get("gb") or 0
    ssd = (boot.get("armazenamento_ssd") or {}).get("gb") or 0
    tipo = "pd-standard" if hdd > ssd else "pd-balanced"
    por_pool = {(p.get("familia"), p.get("regiao")): p for p in posicionamento}
    tamanhos: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for i, r in enumerate(plano.recursos):
        cfg = r.configuration
        if r.resource_type != "compute" or cfg.get("disk_only"):
            continue
        pos = por_pool.get((cfg.get("source_family"), cfg.get("source_region")))
        medio = boot_medio(pos.get("posicionamentos") or []) if pos else None
        da_fatia = cfg.get("slice_boot_gb")
        gb = da_fatia if isinstance(da_fatia, int) and da_fatia > 0 else None
        if gb is None and medio is not None:
            gb = medio[0]
        if gb is None:
            continue
        plano.recursos[i] = replace(r, configuration={**cfg, "root_gb": gb, "root_disk_type": tipo})
        tamanhos[str(cfg.get("pool") or r.logical_name)].append((r.logical_name, gb))
    for pool, lista in tamanhos.items():
        valores = sorted({gb for _, gb in lista})
        faixa = f"{valores[0]} GB" if len(valores) == 1 else f"{valores[0]} a {valores[-1]} GB"
        plano.premissas.append(
            Premissa(
                campo=f"{pool}.root_gb",
                categoria="derivacao",
                valor=faixa,
                base=(
                    "Boot médio da subrede de origem de cada VM no inventário (sem ele, o do "
                    "pool). O boot vira o disco raiz, e não um volume avulso: o billing o cobra "
                    "como volume comum, e o pacote o criaria duas vezes."
                ),
            )
        )


# ── volumes avulsos ─────────────────────────────────────────────────────


def _discos_avulsos(
    plano: PlanoDerivado, medido: Mapping[str, Any], discos: Mapping[str, Any]
) -> None:
    for i, r in reversed(list(enumerate(plano.recursos))):
        cfg = r.configuration
        categoria = str(cfg.get("source_category") or "")
        if not cfg.get("disk_only") or categoria not in _CHAVES_DE_DISCO:
            continue
        conta = avulsos(medido, discos, _CHAVES_DE_DISCO[categoria])
        if conta is None or (conta.gb_boot == 0 and conta.gb_sem_anexo == 0):
            continue
        campo = f"{r.logical_name}.size_gb"
        plano.premissas = [p for p in plano.premissas if p.campo != campo]
        fora = _o_que_saiu(conta)
        if conta.volumes == 0:
            del plano.recursos[i]
            plano.premissas.append(
                Premissa(
                    campo=campo,
                    categoria="derivacao",
                    valor="0 GB",
                    base=f"Todo o {categoria} medido está no inventário: {fora}.",
                )
            )
        else:
            por_volume = min(conta.por_volume, _GB_MAXIMO)
            plano.recursos[i] = replace(
                r, configuration={**cfg, "size_gb": max(1, por_volume), "volumes": conta.volumes}
            )
            plano.premissas.append(
                Premissa(
                    campo=campo,
                    categoria="derivacao",
                    valor=f"{conta.gb:,} GB em {conta.volumes} volumes",
                    base=f"{categoria} medido no billing menos o que o inventário explica: {fora}.",
                )
            )
        plano.avisos.append(
            f"Discos de {categoria}: o pacote cria menos que o medido no billing. {fora}. "
            "O custo aprovado na análise inclui esses discos."
        )


def _o_que_saiu(conta: Avulsos) -> str:
    partes = []
    if conta.gb_boot:
        partes.append(
            f"{conta.gb_boot:,} GB de boot ({conta.volumes_boot} volume(s)) viraram disco "
            "raiz das VMs"
        )
    if conta.gb_sem_anexo:
        partes.append(
            f"{conta.gb_sem_anexo:,} GB sem anexo ({conta.volumes_sem_anexo} volume(s)) não "
            "foram gerados: são ociosos"
        )
    return "; ".join(partes)


# ── bancos ──────────────────────────────────────────────────────────────


def _bancos_sem_grupo(
    plano: PlanoDerivado, sem_grupo: list[Mapping[str, Any]], destino: str
) -> None:
    """Banco que o billing cobrou sem classe sai com a classe do inventário.

    Substitui o banco estimado por razão de custo (o único que o caminho sem
    classe gera); com grupos medidos no plano, os do inventário se somam a eles.
    """
    if not sem_grupo:
        return
    grupos, sem_vcpu = agrupar_sem_grupo(sem_grupo)
    if sem_vcpu:
        plano.avisos.append(
            f"{sem_vcpu} banco(s) do inventário sem classe de instância (Autonomous "
            "Database, por exemplo) seguem com o vCPU estimado pelo custo."
        )
    if not grupos:
        return
    estimados = [
        i
        for i, r in enumerate(plano.recursos)
        if r.resource_type == "managed_database" and not r.configuration.get("source_class")
    ]
    for i in reversed(estimados):
        nome = plano.recursos[i].logical_name
        del plano.recursos[i]
        plano.premissas = [p for p in plano.premissas if not p.campo.startswith(f"{nome}.")]
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    for g in grupos:
        familia = familia_do_motor(g.motor)
        if not motor_aceito(destino, familia):
            plano.nao_suportado.append(
                f"Banco em {destino.upper()} não é gerado para o motor {g.motor}: "
                f"{g.quantidade}x {g.classe} ficou de fora."
            )
            continue
        nome = nomeador.gerar("banco", familia or "motor", g.classe)
        plano.recursos.append(
            RecursoDerivado(
                logical_name=nome,
                resource_type="managed_database",
                configuration={
                    "vcpus": g.vcpus,
                    "instances": g.quantidade,
                    "high_availability": g.alta_disponibilidade,
                    "engine_var": "database_engine",
                    "version_var": f"database_version_{familia}" if familia else "database_version",
                    "vcpu_source": "medido",
                    "source_class": g.classe,
                    "engine_measured": g.motor,
                    "engine_family": familia,
                    "instances_measured": g.quantidade,
                    "source_inventory": True,
                    "source_versions": {g.versao: g.quantidade} if g.versao else {},
                    "source_subnets": list(g.subredes),
                    "source_groups": list(g.grupos),
                },
                depends_on=("rede_principal",),
            )
        )
        plano.premissas.append(
            Premissa(
                campo=f"{nome}.vcpus",
                categoria="derivacao",
                valor=f"{g.vcpus} x{g.quantidade}",
                base=(
                    f"Classe medida no inventário: {g.classe} ({g.quantidade} instância(s), "
                    f"motor {g.motor or 'não informado'}). O billing cobrou o banco sem a "
                    "classe; sem o inventário, o vCPU seria estimado pela razão de custo."
                ),
            )
        )
    if estimados:
        plano.avisos.append(
            "O banco estimado pelo custo foi substituído pelos do inventário, com a classe "
            "medida: " + ", ".join(f"{g.quantidade}x {g.classe}" for g in grupos) + "."
        )


def _versoes(plano: PlanoDerivado, bancos: list[Mapping[str, Any]], destino: str) -> None:
    """A versão medida vira o default da variável de versão (uma por motor)."""
    por_grupo = {
        (b.get("classe"), b.get("motor"), bool(b.get("multi_az")), b.get("regiao")): b.get(
            "versoes"
        )
        or {}
        for b in bancos
    }
    por_variavel: dict[str, list[tuple[str, dict[str, int]]]] = defaultdict(list)
    familia_da_variavel: dict[str, str | None] = {}
    for r in plano.recursos:
        cfg = r.configuration
        if r.resource_type != "managed_database" or not cfg.get("source_class"):
            continue
        medidas = cfg.get("source_versions")
        if cfg.get("source_inventory") and isinstance(medidas, Mapping):
            versoes = dict(medidas)
        else:
            versoes = por_grupo.get(
                (
                    cfg.get("source_class"),
                    cfg.get("engine_measured"),
                    bool(cfg.get("source_multi_az")),
                    cfg.get("source_region"),
                ),
                {},
            )
        variavel = str(cfg.get("version_var") or "database_version")
        por_variavel[variavel].append((r.logical_name, dict(versoes)))
        familia = cfg.get("engine_family")
        familia_da_variavel[variavel] = str(familia) if familia else None
    for variavel, grupos in sorted(por_variavel.items()):
        somadas: dict[str, int] = defaultdict(int)
        for _, versoes in grupos:
            for versao, n in versoes.items():
                somadas[versao] += n
        escolha = versao_padrao(somadas, familia_da_variavel[variavel], destino)
        if escolha.maior is None:
            continue
        if escolha.default is not None:
            plano.variaveis_padrao[variavel] = escolha.default
            _dizer_que_a_versao_veio_do_inventario(plano, grupos, variavel, escolha.default)
        medidas = ", ".join(f"{m} ({n})" for m, n in escolha.contagem.items())
        base = f"Versões maiores medidas no inventário: {medidas}. Default: {escolha.motivo}."
        if len(escolha.contagem) > 1:
            base += (
                " Há instâncias em mais de uma versão: as de outra versão precisam de "
                "upgrade antes da migração, ou de um recurso próprio."
            )
            plano.avisos.append(
                f"`{variavel}`: o inventário tem versões {medidas}; o pacote usa "
                f"{escolha.default or 'nenhum default'} para todas as instâncias do motor."
            )
        plano.premissas.append(
            Premissa(
                campo=f"variavel.{variavel}",
                categoria="derivacao" if escolha.default else "sem_base",
                valor=escolha.default,
                base=base[:800],
            )
        )


def _dizer_que_a_versao_veio_do_inventario(
    plano: PlanoDerivado, grupos: list[tuple[str, dict[str, int]]], variavel: str, default: str
) -> None:
    """A premissa do motor e o comentário do `.tf` diziam "a versão não aparece no
    billing: preencha". Com o default vindo do inventário, isso deixou de ser
    verdade."""
    nomes = {nome for nome, _ in grupos}
    for i, r in enumerate(plano.recursos):
        if r.logical_name in nomes:
            plano.recursos[i] = replace(
                r, configuration={**r.configuration, "version_default": default}
            )
    for i, p in enumerate(plano.premissas):
        nome = p.campo.removesuffix(".engine")
        if nome in nomes and p.campo.endswith(".engine") and p.valor:
            plano.premissas[i] = replace(
                p,
                base=(
                    f"Motor medido no billing: {p.valor}. A versão veio do inventário: "
                    f"`{variavel}` sai com default {default}; confira antes de aplicar."
                ),
            )
