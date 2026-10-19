"""Aplica a topologia do inventário ao plano derivado da análise (RI15).

Roda depois de ``derivar_plano`` e só quando a análise congelou uma
``topologia``. Sem ela, o plano não passa por aqui, e o pacote sai byte a byte
igual ao de antes (teste de caracterização).

O plano de entrada não é alterado: sai um plano novo.
"""

from __future__ import annotations

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
    _buckets_do_escopo(novo, topologia.get("escopo") or {})
    return novo


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
        if tem_banco and portas_no_banco and destino != "aws":
            novos.insert(
                1,
                f"Em {destino.upper()} o banco do pacote fica na subrede de dados, mas sem a "
                "regra de entrada espelhada da origem (só a AWS anexa grupo ao banco): libere "
                f"{', '.join(portas_no_banco)} a partir da rede de origem na subrede de dados, "
                "ou quem ficou na origem não alcança o banco.",
            )
        if sai:
            novos.insert(
                1,
                f"O que migra chama {len({f.get('para') for f in sai})} serviço(s) que ficam "
                f"na origem: libere neles a entrada a partir da rede do destino ({rede}).",
            )
    plano.avisos[:0] = novos
    plano.nao_suportado += [str(a) for a in escopo.get("avisos") or []]


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
