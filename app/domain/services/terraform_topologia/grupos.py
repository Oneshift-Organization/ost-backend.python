"""Grupos de segurança espelhados, com as regras de entrada traduzidas (RI15.4).

Um grupo por grupo de origem com recurso posicionado. Cada regra de entrada é
traduzida para o destino:

- CIDR de subrede espelhada → CIDR da subrede de destino (pedaço de subrede → a
  subrede inteira, com premissa); CIDR interno amplo (várias subredes, a rede
  inteira) → a rede de destino; CIDR externo ou de subrede que não migrou →
  mantido, porque essa origem continua onde está e fala pela VPN.
- Grupo espelhado → o grupo de destino; grupo que não migrou → os CIDRs das
  subredes de origem dos membros; sem membro conhecido, a regra sai com aviso.
- Regra de risco alto (RI11) não é replicada: seria migrar a falha.
- `negar`, protocolo fora de tcp/udp/icmp/todos e serviço do provedor de origem
  sem equivalente (health check, IAP) não são traduzidos, com aviso.

Quando a união de grupos de um recurso passa do limite por interface do destino,
ele recebe um grupo combinado com as regras da união, e toda regra que aceitava
um grupo da união passa a aceitar também o combinado.
"""

from __future__ import annotations

import ipaddress
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.entities.topologia import (
    PROTOCOLOS_CONHECIDOS,
    Acao,
    Direcao,
    RegraDeRede,
    Seletor,
    TipoDeSeletor,
)
from app.domain.services.terraform_plan import Nomeador, PlanoDerivado, Premissa, RecursoDerivado
from app.domain.services.topologia.riscos import regra_de_risco_alto

# Grupos por interface: AWS 5 por ENI, OCI 5 NSGs por VNIC, Azure 20 ASGs por
# configuração de IP, GCP 64 tags por instância.
LIMITE_POR_INTERFACE = {"aws": 5, "oci": 5, "azure": 20, "gcp": 64}

# Faixas de serviço do GCP: são a infraestrutura do Google falando com a VM
# (health check dos balanceadores, IAP). Em outra nuvem não significam nada.
_SERVICOS_DO_GCP = {
    "35.235.240.0/20": "IAP do Google (SSH/RDP)",
    "130.211.0.0/22": "health check dos balanceadores do Google",
    "35.191.0.0/16": "health check dos balanceadores do Google",
    "209.85.152.0/22": "health check legado do Google",
    "209.85.204.0/22": "health check legado do Google",
}
_INTERNET = "0.0.0.0/0"
_MAX_LISTADOS = 5


@dataclass(frozen=True, slots=True)
class RegraTraduzida:
    protocolo: str
    porta_inicio: int | None
    porta_fim: int | None
    cidrs: tuple[str, ...] = ()
    grupos: tuple[str, ...] = ()

    def para_config(self) -> dict[str, Any]:
        return {
            "protocol": self.protocolo,
            "from_port": self.porta_inicio,
            "to_port": self.porta_fim,
            "source_cidrs": list(self.cidrs),
            "source_groups": list(self.grupos),
        }


@dataclass
class _Traducao:
    regras: dict[str, list[RegraTraduzida]] = field(default_factory=lambda: defaultdict(list))
    descartadas: Counter[str] = field(default_factory=Counter)
    exemplos: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    pedacos: int = 0

    def descartar(self, motivo: str, exemplo: str) -> None:
        self.descartadas[motivo] += 1
        exemplos = self.exemplos[motivo]
        if len(exemplos) < _MAX_LISTADOS and exemplo not in exemplos:
            exemplos.append(exemplo)


def espelhar_grupos(
    plano: PlanoDerivado,
    detalhe: Mapping[str, Any],
    *,
    destino: str,
    rede: str,
    provedor_origem: str,
) -> None:
    rede_origem = detalhe.get("rede") or {}
    grupos_origem = {str(g["chave"]): g for g in rede_origem.get("grupos") or [] if g.get("chave")}
    anexos = _anexos(plano, detalhe, destino)
    espelhados = sorted({g for gs in anexos.values() for g in gs if g in grupos_origem})
    if not espelhados:
        return
    nomeador = Nomeador(r.logical_name for r in plano.recursos)
    nomes = {
        g: nomeador.gerar("sg", grupos_origem[g].get("nome") or g.rsplit("/", 1)[-1])
        for g in espelhados
    }
    traducao = _traduzir(
        grupos_origem,
        nomes,
        _mapa_de_subredes(plano),
        rede=rede,
        redes_origem=rede_origem.get("redes_origem") or [],
        subredes_origem=rede_origem.get("subredes_origem") or [],
        referenciados=rede_origem.get("grupos_referenciados") or {},
        provedor_origem=provedor_origem,
    )
    regras = {nomes[g]: _sem_repetir(traducao.regras[nomes[g]]) for g in espelhados}
    anexos_destino = {nome: [nomes[g] for g in gs if g in nomes] for nome, gs in anexos.items()}
    combinados = _combinar(anexos_destino, regras, LIMITE_POR_INTERFACE.get(destino, 5), nomeador)
    _gravar(
        plano,
        espelhados,
        nomes=nomes,
        grupos_origem=grupos_origem,
        regras=regras,
        anexos=anexos_destino,
        combinados=combinados,
    )
    _avisar(
        plano,
        traducao,
        grupos_origem=grupos_origem,
        espelhados=espelhados,
        combinados=combinados,
        destino=destino,
    )
    _avisar_limite_de_regras(plano, destino)


# ── quem recebe qual grupo ──────────────────────────────────────────────


def _anexos(plano: PlanoDerivado, detalhe: Mapping[str, Any], destino: str) -> dict[str, list[str]]:
    """Recurso gerado → grupos de origem que ele herda (VM: a união da subrede;
    banco na AWS com grupo de subredes: os grupos dele na origem)."""
    por_banco = {
        (b.get("classe"), b.get("motor"), bool(b.get("multi_az")), b.get("regiao")): b
        for b in detalhe.get("bancos") or []
    }
    anexos: dict[str, list[str]] = {}
    for r in plano.recursos:
        cfg = r.configuration
        if r.resource_type == "compute" and not cfg.get("disk_only"):
            grupos = _lista(cfg.get("placement_groups"))
        elif r.resource_type == "managed_database" and destino == "aws" and cfg.get("db_subnets"):
            grupos = _lista(cfg.get("source_groups"))
            if not grupos:
                chave = (
                    cfg.get("source_class"),
                    cfg.get("engine_measured"),
                    bool(cfg.get("source_multi_az")),
                    cfg.get("source_region"),
                )
                grupos = _lista((por_banco.get(chave) or {}).get("grupos"))
        else:
            continue
        if grupos:
            anexos[r.logical_name] = sorted(set(grupos))
    return anexos


def _mapa_de_subredes(plano: PlanoDerivado) -> dict[ipaddress.IPv4Network, str]:
    """CIDR de origem → CIDR de destino, das subredes espelhadas."""
    mapa = {}
    for r in plano.recursos:
        origem = r.configuration.get("mirror_of")
        if r.resource_type != "subnet" or not isinstance(origem, dict) or not origem.get("cidr"):
            continue
        try:
            mapa[ipaddress.IPv4Network(str(origem["cidr"]), strict=False)] = str(
                r.configuration["cidr"]
            )
        except ValueError:
            continue
    return mapa


# ── tradução ────────────────────────────────────────────────────────────


def _traduzir(
    grupos_origem: Mapping[str, Mapping[str, Any]],
    nomes: Mapping[str, str],
    subredes: Mapping[ipaddress.IPv4Network, str],
    *,
    rede: str,
    redes_origem: Iterable[str],
    subredes_origem: Iterable[str],
    referenciados: Mapping[str, Mapping[str, Any]],
    provedor_origem: str,
) -> _Traducao:
    t = _Traducao()
    todas_as_subredes = [_rede(c) for c in subredes_origem]
    redes = [_rede(c) for c in redes_origem]
    for chave, nome in nomes.items():
        grupo = grupos_origem[chave]
        rotulo = str(grupo.get("nome") or chave.rsplit("/", 1)[-1])
        for bruta in grupo.get("regras") or []:
            regra = _regra(chave, bruta)
            exemplo = f"{rotulo}: {_portas(regra)}"
            if regra.acao is not Acao.PERMITIR:
                t.descartar("negar", exemplo)
                continue
            if regra.protocolo not in PROTOCOLOS_CONHECIDOS:
                t.descartar("protocolo", exemplo)
                continue
            if regra_de_risco_alto(regra):
                t.descartar("risco_alto", exemplo)
                continue
            alvo = nome
            estreito = regra.destino.valor if regra.destino is not None else None
            if regra.destino is not None and regra.destino.tipo is TipoDeSeletor.GRUPO and estreito:
                alvo = nomes.get(estreito, nome)  # ASG de destino da Azure: estreita o alvo
            origem = _origem(
                regra.origem,
                nomes,
                subredes,
                t,
                rede=rede,
                todas_as_subredes=todas_as_subredes,
                redes=redes,
                referenciados=referenciados,
                provedor_origem=provedor_origem,
                exemplo=exemplo,
            )
            if origem is None:
                continue
            cidrs, grupos = origem
            t.regras[alvo].append(
                RegraTraduzida(regra.protocolo, regra.porta_inicio, regra.porta_fim, cidrs, grupos)
            )
    return t


def _origem(  # noqa: PLR0911 - um retorno por forma de origem
    seletor: Seletor,
    nomes: Mapping[str, str],
    subredes: Mapping[ipaddress.IPv4Network, str],
    t: _Traducao,
    *,
    rede: str,
    todas_as_subredes: list[ipaddress.IPv4Network | None],
    redes: list[ipaddress.IPv4Network | None],
    referenciados: Mapping[str, Mapping[str, Any]],
    provedor_origem: str,
    exemplo: str,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    if seletor.tipo is TipoDeSeletor.QUALQUER:
        return (_INTERNET,), ()
    if seletor.tipo is TipoDeSeletor.GRUPO:
        if seletor.valor is not None and seletor.valor in nomes:
            return (), (nomes[seletor.valor],)
        cidrs = tuple((referenciados.get(seletor.valor or "") or {}).get("cidrs") or ())
        if cidrs:
            return cidrs, ()
        t.descartar("grupo_sem_membro", exemplo)
        return None
    if seletor.tipo is TipoDeSeletor.CIDR:
        cidr = _rede(seletor.valor)
        if cidr is None:
            t.descartar("servico", exemplo)
            return None
        if provedor_origem == "gcp" and str(cidr) in _SERVICOS_DO_GCP:
            t.descartar("servico_do_provedor", f"{exemplo} ({_SERVICOS_DO_GCP[str(cidr)]})")
            return None
        return (
            _cidr_no_destino(
                cidr, subredes, todas_as_subredes=todas_as_subredes, redes=redes, rede=rede, t=t
            ),
        ), ()
    if (seletor.valor or "").casefold() == "virtualnetwork":
        return (rede,), ()
    t.descartar("servico", exemplo)
    return None


def _cidr_no_destino(
    cidr: ipaddress.IPv4Network,
    subredes: Mapping[ipaddress.IPv4Network, str],
    *,
    todas_as_subredes: list[ipaddress.IPv4Network | None],
    redes: list[ipaddress.IPv4Network | None],
    rede: str,
    t: _Traducao,
) -> str:
    for origem, destino in subredes.items():
        if cidr.subnet_of(origem):
            if cidr != origem:
                t.pedacos += 1  # pedaço de subrede: vale a subrede de destino inteira
            return destino
    contidas = [s for s in todas_as_subredes if s is not None and s.subnet_of(cidr)]
    if len(contidas) > 1 or any(r is not None and cidr == r for r in redes):
        return rede  # interno amplo: "qualquer coisa de dentro"
    if len(contidas) == 1 and contidas[0] in subredes:
        return subredes[contidas[0]]
    return str(cidr)  # externo, ou subrede que ficou na origem


# ── limite por interface ────────────────────────────────────────────────


def _combinar(
    anexos: Mapping[str, list[str]],
    regras: dict[str, list[RegraTraduzida]],
    limite: int,
    nomeador: Nomeador,
) -> dict[str, tuple[str, list[str]]]:
    """Recurso → (grupo combinado, grupos que ele substitui), quando passa do limite."""
    combinados: dict[str, tuple[str, list[str]]] = {}
    for recurso, grupos in sorted(anexos.items()):
        if len(grupos) <= limite:
            continue
        nome = nomeador.gerar("sg", recurso.removeprefix("vm_").removeprefix("banco_"))
        regras[nome] = _sem_repetir([r for g in grupos for r in regras.get(g, [])])
        combinados[recurso] = (nome, list(grupos))
    # Quem aceitava um grupo da união passa a aceitar também o combinado.
    for nome_grupo, lista in list(regras.items()):
        regras[nome_grupo] = [
            replace(
                r,
                grupos=tuple(
                    sorted(
                        set(r.grupos)
                        | {
                            c
                            for c, substituidos in combinados.values()
                            if set(r.grupos) & set(substituidos)
                        }
                    )
                ),
            )
            for r in lista
        ]
    return combinados


# ── gravar no plano e avisar ────────────────────────────────────────────


def _gravar(
    plano: PlanoDerivado,
    espelhados: list[str],
    *,
    nomes: Mapping[str, str],
    grupos_origem: Mapping[str, Mapping[str, Any]],
    regras: Mapping[str, list[RegraTraduzida]],
    anexos: Mapping[str, list[str]],
    combinados: Mapping[str, tuple[str, list[str]]],
) -> None:
    subredes_do_grupo: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(plano.recursos):
        if r.logical_name not in anexos:
            continue
        grupos = (
            [combinados[r.logical_name][0]]
            if r.logical_name in combinados
            else anexos[r.logical_name]
        )
        plano.recursos[i] = replace(r, configuration={**r.configuration, "security_groups": grupos})
        for g in grupos:
            subredes_do_grupo[g].add(str(r.configuration.get("subnet") or ""))
    novos = []
    for g in espelhados:
        origem = grupos_origem[g]
        novos.append(_recurso(nomes[g], regras[nomes[g]], subredes_do_grupo, origem.get("nome"), g))
    for nome, substituidos in combinados.values():
        rotulo = "combinado de " + ", ".join(substituidos)
        novos.append(_recurso(nome, regras[nome], subredes_do_grupo, rotulo, None))
    # Depois das subredes e antes de quem os usa.
    posicao = sum(1 for r in plano.recursos if r.resource_type == "subnet")
    plano.recursos[posicao:posicao] = novos


def _recurso(
    nome: str,
    regras: list[RegraTraduzida],
    subredes_do_grupo: Mapping[str, set[str]],
    rotulo: object,
    chave: str | None,
) -> RecursoDerivado:
    return RecursoDerivado(
        logical_name=nome,
        resource_type="security_group",
        configuration={
            "mirror_of": {"chave": chave, "nome": str(rotulo) if rotulo else chave},
            "rules": [r.para_config() for r in regras],
            "subnets": sorted(s for s in subredes_do_grupo.get(nome, set()) if s),
            "combined": chave is None,
        },
        depends_on=("rede_principal",),
    )


_MOTIVOS = {
    "risco_alto": "risco alto (administração, banco ou tudo aberto para a internet): replicar "
    "seria migrar a falha",
    "grupo_sem_membro": "origem é um grupo sem membro conhecido no inventário",
    "servico_do_provedor": "origem é um serviço do provedor de origem, sem equivalente no destino",
    "servico": "origem é um rótulo de serviço sem equivalente (prefix list, AzureLoadBalancer)",
    "negar": "regra de negar: o destino trabalha só com liberação",
    "protocolo": "protocolo fora de tcp, udp, icmp e todos",
}


def _avisar(
    plano: PlanoDerivado,
    t: _Traducao,
    *,
    grupos_origem: Mapping[str, Mapping[str, Any]],
    espelhados: list[str],
    combinados: Mapping[str, tuple[str, list[str]]],
    destino: str,
) -> None:
    for motivo, n in sorted(t.descartadas.items()):
        plano.avisos.append(
            f"{n} regra(s) de entrada não foram replicadas: {_MOTIVOS[motivo]} (ex.: "
            + "; ".join(t.exemplos[motivo])
            + ")."
        )
    restritos = [
        str(grupos_origem[g].get("nome") or g)
        for g in espelhados
        if grupos_origem[g].get("restringe_saida")
    ]
    if restritos:
        plano.avisos.append(
            "Grupos que restringiam a saída na origem saem com a saída liberada (o padrão do "
            "destino): " + ", ".join(restritos[:_MAX_LISTADOS]) + "."
        )
    if combinados:
        plano.avisos.append(
            f"{len(combinados)} recurso(s) herdam mais grupos do que {destino.upper()} aceita "
            f"por interface ({LIMITE_POR_INTERFACE.get(destino)}): cada um recebe um grupo "
            "combinado com as regras da união, e quem aceitava um desses grupos passa a aceitar "
            "também o combinado. Ex.: "
            + ", ".join(nome for nome, _ in list(combinados.values())[:_MAX_LISTADOS])
            + "."
        )
    if destino == "azure":
        plano.avisos.append(
            "Na Azure, a regra padrão AllowVnetInBound libera o tráfego entre as subredes da "
            "mesma VNet: o isolamento entre as redes de origem precisa de uma regra de negar "
            "que o pacote não cria (decisão sua)."
        )
    base = f"{len(espelhados)} grupo(s) de origem com recurso posicionado viraram grupo no destino."
    if t.pedacos:
        base += (
            f" {t.pedacos} regra(s) citavam um pedaço de subrede: no destino valem para a "
            "subrede espelhada inteira."
        )
    if t.descartadas:
        base += f" {sum(t.descartadas.values())} regra(s) não replicadas (ver avisos)."
    plano.premissas.append(
        Premissa(
            campo="grupos_de_seguranca",
            categoria="derivacao",
            valor=f"{len(espelhados)} grupos",
            base=base,
        )
    )


# Regras por grupo que o destino aceita: AWS 60 de entrada por grupo, OCI 120 por
# NSG (a de saída conta), Azure 399 por NSG (prioridade 100 a 4096, de 10 em 10).
_REGRAS_POR_GRUPO = {"aws": 60, "oci": 119}
_REGRAS_POR_NSG_DA_AZURE = 399


def _avisar_limite_de_regras(plano: PlanoDerivado, destino: str) -> None:
    grupos = [r for r in plano.recursos if r.resource_type == "security_group"]
    if destino in _REGRAS_POR_GRUPO:
        limite = _REGRAS_POR_GRUPO[destino]
        cheios = [
            (g.logical_name, n)
            for g in grupos
            if (n := sum(_origens(x) for x in _regras(g))) > limite
        ]
        if cheios:
            plano.avisos.append(
                f"{len(cheios)} grupo(s) passam de {limite} regras, o limite padrão em "
                f"{destino.upper()} (a cota pode ser aumentada): "
                + ", ".join(f"{n} ({q})" for n, q in cheios[:_MAX_LISTADOS])
                + "."
            )
    if destino == "azure":
        por_subrede: Counter[str] = Counter()
        for g in grupos:
            por_regra = sum(
                bool(x.get("source_cidrs")) + bool(x.get("source_groups")) for x in _regras(g)
            )
            for s in _lista(g.configuration.get("subnets")):
                por_subrede[s] += por_regra
        cheias = [s for s, n in por_subrede.items() if n > _REGRAS_POR_NSG_DA_AZURE]
        if cheias:
            plano.avisos.append(
                "NSG com mais regras do que a faixa de prioridade comporta: as que passam de "
                "4096 ficaram de fora nas subredes " + ", ".join(sorted(cheias)) + "."
            )


def _regras(grupo: RecursoDerivado) -> list[Mapping[str, Any]]:
    regras = grupo.configuration.get("rules")
    return [r for r in regras if isinstance(r, Mapping)] if isinstance(regras, list) else []


def _origens(regra: Mapping[str, Any]) -> int:
    return len(regra.get("source_cidrs") or []) + len(regra.get("source_groups") or [])


# ── utilidades ──────────────────────────────────────────────────────────


def _regra(grupo: str, bruta: Mapping[str, Any]) -> RegraDeRede:
    origem = bruta.get("origem") or {}
    destino = bruta.get("destino") or None
    return RegraDeRede(
        grupo=grupo,
        direcao=Direcao.ENTRADA,
        acao=Acao(bruta.get("acao") or Acao.PERMITIR.value),
        protocolo=str(bruta.get("protocolo") or ""),
        origem=Seletor(TipoDeSeletor(origem.get("tipo") or "servico"), origem.get("valor")),
        porta_inicio=bruta.get("porta_inicio"),
        porta_fim=bruta.get("porta_fim"),
        destino=(
            Seletor(TipoDeSeletor(destino["tipo"]), destino.get("valor"))
            if isinstance(destino, Mapping) and destino.get("tipo")
            else None
        ),
    )


def _portas(regra: RegraDeRede) -> str:
    if regra.todas_as_portas:
        return f"{regra.protocolo}/todas"
    if regra.porta_inicio == regra.porta_fim:
        return f"{regra.protocolo}/{regra.porta_inicio}"
    return f"{regra.protocolo}/{regra.porta_inicio}-{regra.porta_fim}"


def _rede(valor: object) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.IPv4Network(str(valor), strict=False)
    except (ValueError, TypeError):
        return None


def _sem_repetir(regras: Iterable[RegraTraduzida]) -> list[RegraTraduzida]:
    vistas: dict[tuple[Any, ...], RegraTraduzida] = {}
    for r in regras:
        chave = (r.protocolo, r.porta_inicio, r.porta_fim, r.cidrs, r.grupos)
        vistas.setdefault(chave, r)
    return sorted(
        vistas.values(),
        key=lambda r: (r.protocolo, r.porta_inicio or 0, r.porta_fim or 0, r.cidrs, r.grupos),
    )


def _lista(valor: object) -> list[str]:
    return [str(x) for x in valor] if isinstance(valor, list | tuple) else []
