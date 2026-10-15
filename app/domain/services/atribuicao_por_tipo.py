"""Custo sem ID de recurso, ligado ao inventário pelo tipo (PLANO_CENARIOS_DE_ENTRADA §3.3).

A fatura resumida (o formato detalhado legado da AWS, a Cost Table do GCP, o CUR
exportado sem IDs) diz quanto custou cada **tipo de uso** — `BoxUsage:t4g.xlarge`,
`InstanceUsage:db.t3.medium`, `AmazonEKS-Hours:perCluster` — mas não de qual
recurso. O inventário diz quais recursos existem e o tipo de cada um. Numa conta
com uma máquina `t4g.xlarge`, um banco `db.t3.medium` e um cluster, a ligação é
praticamente exata.

A regra, por linha:

- acha os recursos do inventário que a linha pode ser: mesmo tipo canônico, o
  atributo que o tipo de uso cita (tipo de máquina, classe do banco, tipo de
  disco), mesma conta e mesma região quando as duas pontas dizem;
- divide o custo: **igual** entre candidatos que cobram igual (mesmo tipo de
  máquina, mesma classe de banco, cluster); **pelo tamanho** entre discos e
  armazenamento de banco; **só com um candidato** onde nada no inventário diz
  quanto cada um consome (vários buckets, vários balanceadores);
- sem candidato, a linha fica como estava: custo sem recurso.

Candidatos que a divisão não sabe separar (duas funções, três buckets) ainda
recebem o custo, em partes iguais, quando estão **todos na mesma unidade**: aí a
divisão não muda o custo da aplicação, que é o que a tela mostra. Quem chama
passa ``unidade_de`` quando já tem a topologia.

Também vale para a linha **com** ID de máquina ou disco que não está no
inventário: o nó que o autoscaling trocou (o EKS que liga de manhã e desliga à
noite cria máquinas novas todo dia) cobra pelo tipo dele, que o inventário tem.

Toda linha atribuída sai marcada ``estimado`` com a base da estimativa: o custo
medido e o estimado nunca se misturam sem dizer.

A tabela de regras é por provedor e é dado, não ``if`` por nuvem. Só a AWS tem
regras hoje: GCP, Azure e OCI detalhados trazem o ID.

Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from app.domain.entities.topologia import Inventario, Recurso, TipoDeRecurso
from app.domain.services.correlacao_inventario import (
    CustoDeRecurso,
    CustosDoBilling,
    IndiceDeApelidos,
    LinhaSemRecurso,
)
from app.domain.services.regioes import regiao_da_linha

_T = TipoDeRecurso
_ZERO = Decimal(0)
_CENTAVO = Decimal("0.000001")

Divisao = Literal["igual", "tamanho", "unico"]

_PREFIXO = r"^(?:[A-Z]{2,4}\d-)?"


@dataclass(frozen=True, slots=True)
class RegraDeTipo:
    servicos: frozenset[str]
    padrao: re.Pattern[str]
    tipo: TipoDeRecurso
    divisao: Divisao
    # Atributo do recurso que tem de bater com o grupo 1 do padrão
    atributo: str | None = None
    # Valor do atributo quando o tipo de uso não cita (`EBS:VolumeUsage` é magnético)
    padrao_do_atributo: str | None = None
    # Peso da divisão por tamanho
    medida: str | None = None
    # Restrição pelo tipo nativo (o NAT é um dos gateways)
    nativo: str | None = None
    # A linha com ID que não está no inventário cai nesta regra (máquina e disco trocam de ID)
    substitui_id: bool = False


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(_PREFIXO + texto + r"$")


_EC2 = frozenset({"AmazonEC2"})
# Serviço inteiro de um tipo de recurso; o CloudFront cobra por edge (`US-`, `EU-`)
_QUALQUER_USO = re.compile(r"^.+$")
REGRAS: Mapping[str, tuple[RegraDeTipo, ...]] = {
    "aws": (
        RegraDeTipo(
            _EC2,
            _r(r"(?:BoxUsage|SpotUsage|DedicatedUsage|HostBoxUsage):([\w.\-]+)"),
            _T.INSTANCIA,
            "igual",
            atributo="tipo_maquina",
            substitui_id=True,
        ),
        RegraDeTipo(
            _EC2,
            _r(r"EBS:VolumeUsage(?:\.(\w+))?"),
            _T.DISCO,
            "tamanho",
            atributo="tipo_disco",
            padrao_do_atributo="standard",
            medida="tamanho_gb",
            substitui_id=True,
        ),
        RegraDeTipo(
            _EC2,
            _r(r"EBS:VolumeP-(?:IOPS|Throughput)\.(\w+)"),
            _T.DISCO,
            "tamanho",
            atributo="tipo_disco",
            medida="tamanho_gb",
            substitui_id=True,
        ),
        RegraDeTipo(_EC2, _r(r"NatGateway-Hours"), _T.GATEWAY, "igual", nativo="NatGateway"),
        RegraDeTipo(_EC2, _r(r"NatGateway-Bytes"), _T.GATEWAY, "unico", nativo="NatGateway"),
        RegraDeTipo(
            frozenset({"AmazonRDS"}),
            _r(
                r"(?:InstanceUsage|Multi-AZUsage|Multi-AZClusterUsage)(?:IOOptimized)?:(db\.[\w.]+)"
            ),
            _T.BANCO,
            "igual",
            atributo="classe",
        ),
        RegraDeTipo(
            frozenset({"AmazonRDS"}),
            _r(r"(?:RDS|Aurora):[\w-]*(?:Storage|Backup|IOUsage)[\w-]*"),
            _T.BANCO,
            "tamanho",
            medida="armazenamento_gb",
        ),
        RegraDeTipo(
            frozenset({"AmazonEKS"}), _r(r"AmazonEKS-Hours:\w+"), _T.CLUSTER_KUBERNETES, "igual"
        ),
        RegraDeTipo(
            frozenset({"AWSELB"}),
            _r(r"(?:LoadBalancerUsage|LCUUsage|NLCUUsage|DataProcessing-Bytes)"),
            _T.BALANCEADOR,
            "unico",
        ),
        RegraDeTipo(
            frozenset({"AmazonS3"}),
            _r(r"(?:TimedStorage|Requests|EarlyDelete|Retrieval|TagStorage|Monitoring)-[\w-]+"),
            _T.BUCKET,
            "unico",
        ),
        RegraDeTipo(
            frozenset({"AmazonElastiCache"}),
            _r(r"NodeUsage:(cache\.[\w.]+)"),
            _T.CACHE,
            "igual",
            atributo="classe",
        ),
        # Componentes (atributo `componente`): o serviço inteiro é deles
        *(
            RegraDeTipo(frozenset({servico}), _QUALQUER_USO, _T.OUTRO, "unico", nativo=nativo)
            for nativo, servico in (
                ("AWS::Lambda::Function", "AWSLambda"),
                ("AWS::CloudFront::Distribution", "AmazonCloudFront"),
                ("AWS::DynamoDB::Table", "AmazonDynamoDB"),
                ("AWS::ECR::Repository", "AmazonECR"),
                ("AWS::SecretsManager::Secret", "AWSSecretsManager"),
                ("AWS::SQS::Queue", "AWSQueueService"),
                ("AWS::SNS::Topic", "AmazonSNS"),
            )
        ),
    ),
}

# O serviço da fatura que cada tipo de recurso gera. Liga serviço a unidade
# mesmo quando o custo não pôde ser dividido (três buckets, uma linha de S3).
SERVICO_DO_TIPO: Mapping[str, Mapping[TipoDeRecurso, str]] = {
    "aws": {
        _T.INSTANCIA: "AmazonEC2",
        _T.DISCO: "AmazonEC2",
        _T.BANCO: "AmazonRDS",
        _T.CACHE: "AmazonElastiCache",
        _T.BALANCEADOR: "AWSELB",
        _T.BUCKET: "AmazonS3",
        _T.CLUSTER_KUBERNETES: "AmazonEKS",
    },
}
# Componente: o tipo canônico é "outro", o nativo diz o serviço
SERVICO_DO_NATIVO: Mapping[str, str] = {
    "AWS::Lambda::Function": "AWSLambda",
    "AWS::CloudFront::Distribution": "AmazonCloudFront",
    "AWS::DynamoDB::Table": "AmazonDynamoDB",
    "AWS::ECR::Repository": "AmazonECR",
    "AWS::SecretsManager::Secret": "AWSSecretsManager",
    "AWS::SQS::Queue": "AWSQueueService",
    "AWS::SNS::Topic": "AmazonSNS",
}


def servicos_do_recurso(provedor: str | None, recursos: Iterable[Recurso]) -> set[str]:
    tabela = SERVICO_DO_TIPO.get((provedor or "").lower(), {})
    return {
        s for r in recursos if (s := SERVICO_DO_NATIVO.get(r.tipo_nativo) or tabela.get(r.tipo))
    }


@dataclass(frozen=True, slots=True)
class _Linha:
    conta: str | None
    regiao: str | None
    servico: str
    uso: str
    custo: Decimal
    quantidade: Decimal | None
    origem: str | None  # o ID que a fatura trazia, quando trazia


def atribuir_por_tipo(
    inventario: Inventario,
    custos: CustosDoBilling,
    *,
    unidade_de: Callable[[str], str | None] | None = None,
) -> CustosDoBilling:
    """``custos`` com as linhas que dá para ligar pelo tipo já ligadas a um recurso.

    A linha atribuída sai com ``resource_id`` igual à chave do recurso no inventário
    (um dos apelidos dele), ``estimado=True`` e a ``base`` da estimativa. As que
    não deu continuam onde estavam.
    """
    provedor = (custos.provedor or inventario.provedor or "").lower()
    regras = REGRAS.get(provedor, ())
    if not regras or not inventario.recursos:
        return custos
    apelidos = IndiceDeApelidos(inventario)
    por_tipo: dict[TipoDeRecurso, list[Recurso]] = defaultdict(list)
    for r in inventario.recursos:
        por_tipo[r.tipo].append(r)

    por_recurso: list[CustoDeRecurso] = []
    for linha in custos.por_recurso:
        if linha.estimado or apelidos.candidatos(linha.resource_id) or not linha.uso:
            por_recurso.append(linha)
            continue
        estimadas = _estimar(
            _Linha(
                conta=linha.conta,
                regiao=regiao_da_linha(provedor, linha.regiao, linha.uso),
                servico=linha.servico,
                uso=linha.uso,
                custo=linha.custo,
                quantidade=linha.quantidade,
                origem=linha.resource_id,
            ),
            regras,
            por_tipo,
            so_quem_troca_de_id=True,
            unidade_de=unidade_de,
        )
        por_recurso.extend(estimadas or [linha])

    sem_recurso: list[LinhaSemRecurso] = []
    for s in custos.sem_recurso:
        estimadas = (
            _estimar(
                _Linha(
                    conta=s.conta,
                    regiao=regiao_da_linha(provedor, s.regiao, s.uso),
                    servico=s.servico,
                    uso=s.uso,
                    custo=s.custo,
                    quantidade=s.quantidade,
                    origem=None,
                ),
                regras,
                por_tipo,
                so_quem_troca_de_id=False,
                unidade_de=unidade_de,
            )
            if s.uso
            else []
        )
        if estimadas:
            por_recurso.extend(estimadas)
        else:
            sem_recurso.append(s)
    return replace(custos, por_recurso=tuple(por_recurso), sem_recurso=tuple(sem_recurso))


def _estimar(
    linha: _Linha,
    regras: tuple[RegraDeTipo, ...],
    por_tipo: Mapping[TipoDeRecurso, list[Recurso]],
    *,
    so_quem_troca_de_id: bool,
    unidade_de: Callable[[str], str | None] | None,
) -> list[CustoDeRecurso]:
    for regra in regras:
        if so_quem_troca_de_id and not regra.substitui_id:
            continue
        if linha.servico not in regra.servicos:
            continue
        m = regra.padrao.match(linha.uso)
        if m is None:
            continue
        valor = (m.group(1) if m.groups() else None) or regra.padrao_do_atributo
        candidatos = [
            r
            for r in por_tipo.get(regra.tipo, ())
            if _mesmo_lugar(r, linha)
            and (regra.nativo is None or r.tipo_nativo.endswith(regra.nativo))
            and (
                regra.atributo is None
                or str(r.atributos.get(regra.atributo) or "").lower() == (valor or "").lower()
            )
        ]
        pesos = _pesos(regra, candidatos)
        mesma_unidade = False
        if not pesos and len(candidatos) > 1 and unidade_de is not None:
            unidades = {unidade_de(r.chave) for r in candidatos}
            if len(unidades) == 1 and None not in unidades:
                pesos, mesma_unidade = [(r, Decimal(1)) for r in candidatos], True
        if not pesos:
            return []
        return _dividir(linha, regra, valor, pesos, mesma_unidade=mesma_unidade)
    return []


def _mesmo_lugar(r: Recurso, linha: _Linha) -> bool:
    if linha.conta and r.conta and linha.conta != r.conta:
        return False
    return not (linha.regiao and r.regiao and linha.regiao != r.regiao)


def _pesos(regra: RegraDeTipo, candidatos: list[Recurso]) -> list[tuple[Recurso, Decimal]]:
    if not candidatos:
        return []
    if len(candidatos) == 1:
        return [(candidatos[0], Decimal(1))]
    if regra.divisao == "igual":
        return [(r, Decimal(1)) for r in candidatos]
    if regra.divisao == "tamanho" and regra.medida:
        pesos = [(r, _numero(r.atributos.get(regra.medida))) for r in candidatos]
        if all(p > 0 for _, p in pesos):
            return pesos
    return []


def _numero(valor: object) -> Decimal:
    try:
        return Decimal(str(valor)) if valor is not None else _ZERO
    except ArithmeticError:
        return _ZERO


def _dividir(
    linha: _Linha,
    regra: RegraDeTipo,
    valor: str | None,
    pesos: list[tuple[Recurso, Decimal]],
    *,
    mesma_unidade: bool = False,
) -> list[CustoDeRecurso]:
    total = sum((p for _, p in pesos), _ZERO)
    nome = regra.nativo or regra.tipo.value
    descricao = f"{nome} {valor}" if valor else nome
    if mesma_unidade:
        descricao += ", todos na mesma aplicação"
    base = (
        f"{linha.uso}: {descricao}"
        if len(pesos) == 1
        else f"{linha.uso}: dividido entre {len(pesos)} ({descricao}"
        + (", pelo tamanho)" if regra.divisao == "tamanho" else ")")
    )
    saida = []
    resto_custo = linha.custo
    resto_qtd = linha.quantidade
    for i, (r, peso) in enumerate(sorted(pesos, key=lambda rp: rp[0].chave)):
        ultimo = i == len(pesos) - 1
        custo = resto_custo if ultimo else (linha.custo * peso / total).quantize(_CENTAVO)
        quantidade = (
            None
            if linha.quantidade is None
            else resto_qtd
            if ultimo
            else (linha.quantidade * peso / total).quantize(_CENTAVO)
        )
        resto_custo -= custo
        if resto_qtd is not None and quantidade is not None:
            resto_qtd -= quantidade
        saida.append(
            CustoDeRecurso(
                resource_id=r.chave,
                conta=linha.conta or r.conta,
                regiao=r.regiao or linha.regiao,
                servico=linha.servico,
                custo=custo,
                uso=linha.uso,
                quantidade=quantidade,
                estimado=True,
                origem=linha.origem,
                base=base,
            )
        )
    return saida
