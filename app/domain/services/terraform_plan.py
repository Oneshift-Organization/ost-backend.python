"""Deriva a infraestrutura de destino a partir da análise. Sem IA.

## Por que isto existe

A especificação recebida pedia que o modelo "transformasse a análise num
blueprint". Mas a maior parte do blueprint não é opinião — é conta:

- o shape da máquina (`n2-standard-8`) foi escolhido pelo packer;
- a quantidade de instâncias saiu do mesmo cálculo;
- vCPU e RAM vêm do pool agregado;
- GB de disco sai de `custo ÷ taxa da categoria`.

Pedir isso a um modelo troca uma resposta certa por uma plausível, e torna o
resultado não-determinístico: duas execuções podem agrupar diferente. Num
artefato que o cliente vai aplicar na infraestrutura dele, isso é inaceitável.

Então o backend deriva, e o modelo fica com o que é genuinamente julgamento —
nomes legíveis, agrupamento, texto. Efeito colateral: **se o modelo cair, ainda
sai um `.tf` correto**, com nomes feios. O modelo deixa de ser ponto único de
falha.

## O que cada valor carrega

Toda derivação que não é conta exata emite uma `Premissa` com a base. Um `.tf`
parece preciso; sem marcar o que foi estimado, o cliente lê estimativa como
fato. É a mesma regra do `payback` na superfície de agente.

Módulo puro: entra dict, sai dataclass. Sem I/O, sem framework.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.services.categoria_por_uso import (
    BASTION_SEGURANCA_E_SERVERLESS,
    OBSERVABILIDADE_E_BORDA,
)
from app.domain.services.categoria_por_uso import CHAVE_NO_PAYLOAD as CHAVE_POR_USO
from app.domain.services.instance_shape import parse_instance_shape
from app.domain.services.perfil_de_familia import perfil

# Taxas de referência por GB-mês, usadas para voltar de dinheiro para tamanho.
# São as mesmas de `seed_category_rates`; ficam aqui porque a derivação é pura e
# não consulta banco. Divergir delas não quebra nada — muda o GB estimado, que
# já está declarado como estimativa.
_TAXA_GB_MES: dict[str, dict[str, str]] = {
    "aws": {"storage_ssd": "0.080", "storage_hdd": "0.045", "object_storage": "0.023"},
    "gcp": {"storage_ssd": "0.100", "storage_hdd": "0.040", "object_storage": "0.020"},
    "azure": {"storage_ssd": "0.0803", "storage_hdd": "0.045", "object_storage": "0.018"},
    # SSD = Block Volume Balanced, armazenamento + 10 VPU (PLANO_PRECISAO_OCI P1.2)
    "oci": {"storage_ssd": "0.0425", "storage_hdd": "0.0255", "object_storage": "0.0255"},
}

# Chaves de `custos_auxiliares` com serviço de rede pago (rede por tipo, PLANO_PRECO_NAO_COMPUTE)
_REDE_PAGA = (
    "balanceador",
    "balanceador_dados",
    "nat",
    "nat_dados",
    "ip_publico",
    "vpn",
    "transferencia_entre_zonas",
    "cdn",
    "cdn_requisicoes",
    "rede_outros",
)

# $/vCPU-mês de banco gerenciado, general-purpose. Mesma origem.
_TAXA_DB_VCPU_MES: dict[str, str] = {
    "aws": "62",
    "gcp": "60",
    "azure": "73",
    "oci": "30",
}

# O plano descreve o disco com os nomes do GCP, que são o vocabulário neutro da
# derivação; cada renderer traduz a classe. SSD é o pd-balanced, o de propósito
# geral: é por ele que a taxa de referência cota, e é o par do gp3 e do Premium
# SSD. Provisionar pd-ssd cobraria o tier de desempenho que a cotação não previu.
TIPO_DE_DISCO: dict[str, str] = {"storage_ssd": "pd-balanced", "storage_hdd": "pd-standard"}

# Chave do payload → categoria interna.
_CATEGORIAS_ARMAZENAMENTO = {
    "armazenamento_ssd": "storage_ssd",
    "armazenamento_hdd": "storage_hdd",
    "armazenamento_objetos": "object_storage",
}

# Disco mínimo que vale a pena declarar. Abaixo disto o valor é ruído de
# arredondamento da divisão por taxa, e um volume de 2 GB no `.tf` seria mais
# enganoso que a omissão.
_GB_MINIMO = Decimal("10")

# Motores de CACHE: aparecem no inventário de bancos (ElastiCache, Memorystore),
# mas não viram banco relacional gerenciado — seria outro produto.
_MOTORES_DE_CACHE = frozenset({"redis", "memcached", "valkey"})

# Grupos de banco medidos que viram recurso; o resto é listado nos avisos.
_MAX_GRUPOS_BANCO = 8

# Motor medido → família (decide a variável de versão e a compatibilidade).
_FAMILIAS_DE_MOTOR = (
    ("postgres", "postgresql"),
    ("mysql", "mysql"),
    ("mariadb", "mysql"),
    ("sql server", "sqlserver"),
    ("sqlserver", "sqlserver"),
    ("oracle", "oracle"),
)

# O que o renderer de cada destino sabe gerar, cada motor no produto dele. Ausente =
# aceita qualquer um. Recusar aqui, e não no renderer, é o que faz a recusa aparecer
# no README em vez de sumir em silêncio.
_MOTORES_POR_DESTINO: dict[str, frozenset[str]] = {
    "azure": frozenset({"postgresql"}),
    "gcp": frozenset({"postgresql", "mysql", "sqlserver"}),
    "aws": frozenset({"postgresql", "mysql", "sqlserver", "oracle"}),
    # PostgreSQL, MySQL HeatWave e Autonomous (Oracle); a OCI não tem SQL Server gerenciado
    "oci": frozenset({"postgresql", "mysql", "oracle"}),
}


def motor_aceito(destino: str, familia: str | None) -> bool:
    aceitos = _MOTORES_POR_DESTINO.get(destino)
    return aceitos is None or familia in aceitos


# Tempo ligada abaixo disto sugere agendamento (dev, horário comercial).
_TEMPO_ATIVO_BAIXO = 0.6

# Custo que sobra num balde de disco depois de tirar o snapshot e que ainda
# justificaria um volume. Abaixo disto é arredondamento.
_CUSTO_RESIDUAL = Decimal("1")

# Quantos itens listar num aviso antes de resumir com reticências.
_MAX_LISTADOS = 5

# Confiança do preço que sustenta a escolha do shape. Só `official` é preço
# coletado da API do provedor; `reference` é taxa publicada e `low` é catálogo
# interno. O packer escolhe a máquina COMPARANDO PREÇOS — se o preço é fraco, a
# escolha herda a fraqueza, e chamá-la de "calculada" seria afirmar mais do que
# sabemos.
_CONFIANCA_OFICIAL = "official"


def _tipo_de_maquina(sku: str) -> str:
    """SKU de faturamento → identificador de deploy.

    O catálogo guarda o SKU como ele aparece no bill — `BoxUsage:m7g.medium`,
    `USE1-SpotUsage:m7g.2xlarge` — porque é assim que a linha de faturamento
    casa. Nenhuma nuvem aceita isso como tipo de máquina.

    Atingia 65 dos 71 SKUs de AWS do catálogo. Passava no `terraform validate`
    (o campo é string livre) e falharia no apply com "Invalid instance type".

    Fica aqui, e não no renderer, para que a **premissa** mostre o mesmo valor
    que o `.tf`. Com a normalização no renderer, o README dizia
    `SpotUsage:m7g.2xlarge` e o arquivo dizia `m7g.2xlarge` — o cliente não
    conseguia reconciliar os dois.
    """
    return sku.rsplit(":", 1)[-1].strip() or sku


# Teto de um persistent disk no GCP. O balde de armazenamento é o TOTAL da conta,
# somando muitos volumes na origem; representá-lo como um disco só já é
# simplificação, e acima deste limite a simplificação deixa de ser aplicável —
# o `terraform validate` passa (ele não confere faixa de valor) e o `apply`
# falha na cara do cliente.
_GB_MAXIMO_DISCO = Decimal("65536")


@dataclass(frozen=True, slots=True)
class Premissa:
    campo: str
    categoria: str
    valor: str | None
    base: str


@dataclass(frozen=True, slots=True)
class RecursoDerivado:
    logical_name: str
    resource_type: str
    configuration: dict[str, object]
    depends_on: tuple[str, ...] = ()


@dataclass
class PlanoDerivado:
    recursos: list[RecursoDerivado] = field(default_factory=list)
    premissas: list[Premissa] = field(default_factory=list)
    nao_suportado: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    # Default de variável que a topologia do inventário sabe (versão do banco).
    variaveis_padrao: dict[str, str] = field(default_factory=dict)


def derivar_plano(
    payload: dict[str, Any],
    *,
    provedor_destino: str,
    regiao: str,
    alta_disponibilidade: bool = False,
) -> PlanoDerivado:
    """Converte o payload determinístico da análise em recursos.

    ``payload`` é o `llm_input_payload` gravado no job — a análise que o usuário
    aprovou na tela. **Não** se reconstrói o contexto: recalcular pode produzir
    números diferentes, porque preço, câmbio e catálogo mudam entre execuções.
    """
    plano = PlanoDerivado()
    destino = provedor_destino.lower()
    nomeador = Nomeador()

    _derivar_compute(payload, destino, plano, nomeador)
    _derivar_armazenamento(payload, destino, regiao, plano, nomeador)
    _derivar_banco(payload, destino, plano, alta_disponibilidade, nomeador)
    _registrar_lacunas(payload, plano)

    if not plano.recursos:
        plano.avisos.append(
            "Nenhum recurso derivado: a análise não tem equivalente para o "
            "provedor escolhido. O pacote sai só com rede e variáveis."
        )
    return plano


# ── compute ─────────────────────────────────────────────────────────────


def _derivar_compute(
    payload: dict[str, Any],
    destino: str,
    plano: PlanoDerivado,
    nomeador: Nomeador,
) -> None:
    """Um recurso por pool de família, com o shape que o packer escolheu.

    O pool já é a unidade de comparação cross-cloud: agregado por (família,
    região), com a capacidade total. Um recurso por pool preserva essa estrutura
    em vez de inventar uma divisão que a análise não fez.
    """
    for pool in payload.get("pools_computacao") or []:
        familia = str(pool.get("familia") or "pool")
        equivalente = _equivalente_do_destino(pool.get("equivalentes") or [], destino)

        if equivalente is None:
            plano.nao_suportado.append(
                f"Pool de compute '{familia}': a análise não encontrou "
                f"equivalente em {destino.upper()}."
            )
            continue

        sku_bruto = equivalente.get("sku")
        sku = _tipo_de_maquina(str(sku_bruto)) if sku_bruto else None
        # A região entra no nome porque o pool é chaveado por (família, região):
        # sem ela, a mesma família em duas regiões vira o mesmo identificador.
        nome = nomeador.gerar("vm", familia, pool.get("regiao"))

        if not sku_bruto:
            # Equivalente do catálogo curado: sabemos o serviço, não o shape.
            # Vira variável em branco — nunca um shape inventado.
            plano.premissas.append(
                Premissa(
                    campo=f"{nome}.machine_type",
                    categoria="sem_base",
                    valor=None,
                    base=(
                        f"O equivalente de '{familia}' veio do catálogo curado "
                        "(sem correspondência de hardware), então não há shape "
                        "calculado. Preencha a variável antes de aplicar."
                    ),
                )
            )
            continue

        instancias = int(pool.get("instancias") or equivalente.get("instancias") or 1)
        tecnica = equivalente.get("correspondencia_tecnica") or {}
        linha_de_base = _linha_de_base(payload, pool, destino, str(sku_bruto))
        capacidade = _pela_linha_de_base(pool.get("capacidade") or {}, linha_de_base)
        ocupacao = _ocupacao(capacidade, tecnica)

        plano.recursos.append(
            RecursoDerivado(
                logical_name=nome,
                resource_type="compute",
                configuration={
                    "machine_type": sku,
                    "instances": max(1, instancias),
                    "vcpus": tecnica.get("vcpus"),
                    "ram_gb": tecnica.get("ram_gb"),
                    "source_family": familia,
                    # Chave do pool (família, região): é por ela que a topologia
                    # acha o posicionamento. Não é renderizada.
                    "source_region": pool.get("regiao"),
                },
                depends_on=("subnet_principal",),
            )
        )
        financeiro = equivalente.get("financeiro") or {}
        confianca = str(financeiro.get("confianca_preco") or "low").strip().lower()
        oficial = confianca == _CONFIANCA_OFICIAL

        base = (
            f"Escolhido pelo empacotamento da capacidade do pool '{familia}' "
            f"({tecnica.get('vcpus')} vCPU, {tecnica.get('ram_gb')} GB)."
        )
        if not oficial:
            origem = financeiro.get("preco_origem") or "origem não declarada"
            base += (
                f" O empacotamento compara PREÇOS, e o preço usado tem confiança "
                f"'{confianca}' ({origem}) — não é preço coletado da API do "
                "provedor. Outra máquina pode sair melhor com preço oficial."
            )
        idade = financeiro.get("preco_idade_dias")
        if idade is not None:
            base += f" Preço capturado há {idade} dias."
        base += _origem_medida(pool, familia, max(1, instancias), plano)
        if linha_de_base is not None:
            base += (
                f" A origem é burstable: a vCPU conta pela linha de base "
                f"({linha_de_base:.0%} da nominal), como no empacotamento."
            )
        base += _aviso_de_empacotamento(
            familia,
            max(1, instancias),
            ocupacao,
            plano,
            instancias_na_origem=pool.get("instancias_medidas"),
            capacidade=capacidade,
            tecnica=tecnica,
        )
        if pool.get("custo_coberto_por_compromisso") or pool.get(
            "coberto_por_compromisso_estimado"
        ):
            # O recurso está certo — a capacidade foi medida. O que não vale é a
            # comparação de custo contra ele.
            base += (
                " ATENÇÃO: na origem este pool aparece com custo zero porque as "
                "instâncias são cobertas por compromisso (Savings Plan, RI ou "
                "CUD). O dimensionamento é confiável; qualquer economia "
                "calculada contra ele, não."
            )

        plano.premissas.append(
            Premissa(
                campo=f"{nome}.machine_type",
                # Derivação só quando o insumo sustenta: a conta é exata, mas o
                # preço que ela compara pode não ser.
                categoria="derivacao" if oficial else "estimativa",
                valor=f"{sku} x{max(1, instancias)}",
                base=base,
            )
        )


# Fora desta faixa, o número de máquinas pede atenção: muita sobra, ou o uso médio
# já passa do que se provisiona (T1)
_OCUPACAO_BAIXA = Decimal("0.5")
_OCUPACAO_CHEIA = Decimal("1")
# Máquina do destino abaixo disto da média da origem: o empacotamento fragmentou
_MAQUINA_MENOR = Decimal("0.9")


def _linha_de_base(
    payload: dict[str, Any], pool: dict[str, Any], destino: str, sku: str
) -> Decimal | None:
    """A base com que o empacotamento dimensionou o pool (`_packing_pela_base`): a que a
    análise gravou em `premissas.burstable_pela_base`, só para destino sem crédito.
    Análise anterior a essa regra não grava nada e foi dimensionada pela nominal: None."""
    registro = ((payload.get("premissas") or {}).get("burstable_pela_base") or {}).get("pools")
    chave = f"{pool.get('familia')}/{pool.get('regiao')}"
    base = next(
        (_decimal(p.get("base_cpu")) for p in registro or [] if p.get("pool") == chave), None
    )
    if base is None:
        return None
    no_destino = perfil(destino, sku)
    if no_destino is not None and no_destino.burstable:
        return None
    return base


def _pela_linha_de_base(capacidade: dict[str, Any], base: Decimal | None) -> dict[str, Any]:
    vcpus = _decimal(capacidade.get("vcpus"))
    if base is None or vcpus is None:
        return capacidade
    return {**capacidade, "vcpus": vcpus * base}


def _ocupacao(capacidade: dict[str, Any], tecnica: dict[str, Any]) -> Decimal | None:
    """Que fração das máquinas provisionadas o uso medido ocupa.

    A cotação é pela capacidade-hora: o pool paga a fração que usa. O Terraform
    provisiona máquinas inteiras. Vale a dimensão que mais aperta (vCPU ou memória)."""
    fracoes = [
        usado / provisionado
        for usado, provisionado in (
            (_decimal(capacidade.get("vcpus")), _decimal(tecnica.get("vcpus"))),
            (_decimal(capacidade.get("ram_gib")), _decimal(tecnica.get("ram_gb"))),
        )
        if usado is not None and provisionado is not None
    ]
    return max(fracoes) if fracoes else None


def _aviso_de_empacotamento(
    familia: str,
    instancias: int,
    ocupacao: Decimal | None,
    plano: PlanoDerivado,
    *,
    instancias_na_origem: object,
    capacidade: dict[str, Any],
    tecnica: dict[str, Any],
) -> str:
    """"N máquinas provisionadas; o uso medido ocupa X% delas" (T1).

    Avisa quando a ocupação sai da faixa, e quando o empacotamento quebra poucas
    máquinas grandes em muitas pequenas (2 FSV2 de 16 vCPU viram 9 de 4): a soma é a
    mesma, mas cada processo precisa caber numa máquina pequena."""
    if ocupacao is None:
        return ""
    uma = instancias == 1
    maquinas = "máquina provisionada" if uma else "máquinas provisionadas"
    parte = "menos de 1%" if ocupacao < Decimal("0.005") else f"{ocupacao:.0%}"
    texto = f"{instancias} {maquinas}; o uso medido ocupa {parte} {'dela' if uma else 'delas'}."
    na_origem = _decimal(instancias_na_origem)
    usado = _decimal(capacidade.get("vcpus"))
    vcpus = _decimal(tecnica.get("vcpus"))
    destino = vcpus / instancias if vcpus else None
    origem = usado / na_origem if usado and na_origem else None
    if destino is not None and origem is not None and destino < _MAQUINA_MENOR * origem:
        plano.avisos.append(
            f"Pool '{familia}': {_quantas(na_origem)} de ~{origem:.0f} vCPU na origem "
            f"{'vira' if na_origem == 1 else 'viram'} {instancias} de "
            f"{destino.normalize():f} vCPU no destino; o uso medido "
            f"ocupa {parte} delas. A capacidade somada é a mesma, mas cada processo "
            "precisa caber numa máquina menor: confirme antes de aplicar."
        )
    elif ocupacao > _OCUPACAO_CHEIA:
        plano.avisos.append(
            f"Pool '{familia}': {texto} O uso médio passa do que se provisiona: "
            "picos ficam sem folga. Avalie uma máquina a mais antes de aplicar."
        )
    elif ocupacao < _OCUPACAO_BAIXA:
        plano.avisos.append(
            f"Pool '{familia}': {texto} Sobra capacidade: a cotação considerou só a "
            "fração usada, mas o destino cobra as máquinas inteiras."
        )
    return f" {texto}"


def _quantas(instancias: Decimal) -> str:
    n = instancias.normalize()
    return f"{n:f} instância" if n == 1 else f"{n:f} instâncias"


def _origem_medida(
    pool: dict[str, Any], familia: str, instancias_destino: int, plano: PlanoDerivado
) -> str:
    """O que o export detalhado mediu na origem, dito na premissa.

    O shape de destino continua o do empacotamento — é o que foi cotado (RN10).
    Mas quando ele junta N máquinas em menos, isso é uma decisão de arquitetura,
    não só de preço: vira aviso explícito.
    """
    instancias = pool.get("instancias_medidas")
    if not instancias:
        return ""
    tipos = pool.get("tipos_origem") or {}
    lista = ", ".join(f"{n}x {t}" for t, n in list(tipos.items())[:3])
    texto = f" Origem medida: {instancias} instâncias ({lista})."
    if instancias_destino < int(instancias):
        plano.avisos.append(
            f"Pool '{familia}': {instancias} instâncias na origem viram "
            f"{instancias_destino} no destino (consolidação do empacotamento). "
            "Confirme que as aplicações podem dividir máquina antes de aplicar."
        )
    ativo = pool.get("fracao_tempo_ativo")
    if isinstance(ativo, (int, float)) and ativo < _TEMPO_ATIVO_BAIXO:
        texto += f" Ficam ligadas {ativo:.0%} do tempo em média: avalie agendamento no destino."
    return texto


def _equivalente_do_destino(
    equivalentes: list[dict[str, Any]], destino: str
) -> dict[str, Any] | None:
    """Prefere equivalente com shape ao curado, quando há os dois.

    Sem isto, a ordem da lista decidiria — e um equivalente curado apagaria um
    com hardware casado só por vir antes.
    """
    candidatos = [e for e in equivalentes if str(e.get("provedor", "")).lower() == destino]
    if not candidatos:
        return None
    com_sku = [e for e in candidatos if e.get("sku")]
    return (com_sku or candidatos)[0]


# ── armazenamento ───────────────────────────────────────────────────────


def _derivar_armazenamento(
    payload: dict[str, Any],
    destino: str,
    regiao: str,
    plano: PlanoDerivado,
    nomeador: Nomeador,
) -> None:
    """Volta de dinheiro para tamanho: ``GB = custo ÷ taxa``.

    É estimativa, não conta: assume que o balde inteiro está na tier padrão. Um
    bill com muito Glacier ou Nearline sai superdimensionado, porque a taxa fria
    é bem menor. Por isso cada volume emite a premissa com a taxa usada.
    """
    auxiliares = payload.get("custos_auxiliares") or {}
    origem = str(payload.get("provedor_origem") or "").lower()
    taxas_origem = _TAXA_GB_MES.get(origem)
    medido = (payload.get("inventario_medido") or {}).get("armazenamento") or {}
    _registrar_snapshots(medido, plano)

    if taxas_origem is None and not medido:
        # Sem a taxa da ORIGEM não há como voltar de dinheiro para GB. Avisa só
        # quando havia custo a dimensionar — senão o pacote de um bill sem
        # armazenamento sairia com um aviso sobre nada.
        if auxiliares:
            plano.avisos.append(
                f"Provedor de origem '{origem or 'desconhecido'}' não tem taxa de "
                "armazenamento conhecida; volumes não foram dimensionados."
            )
        return

    for chave, categoria in _CATEGORIAS_ARMAZENAMENTO.items():
        custo = _decimal(auxiliares.get(chave))
        if custo is None or custo <= 0:
            continue

        # Snapshot medido nesta categoria sai do custo antes de estimar: ele é
        # backup, não disco. Se sobrar ~nada, a categoria não tem volume por trás.
        snapshot = _decimal(((medido.get("snapshots") or {}).get("custo") or {}).get(chave))
        if snapshot is not None:
            custo = custo - snapshot
            if custo <= _CUSTO_RESIDUAL:
                continue
        tamanho = _tamanho_armazenamento(medido.get(chave) or {}, custo, taxas_origem, categoria)
        if tamanho is None:
            continue
        gb, taxa, volumes, gb_medido = tamanho

        if categoria == "object_storage":
            nome = nomeador.gerar("bucket", categoria)
            plano.recursos.append(
                RecursoDerivado(
                    logical_name=nome,
                    resource_type="object_storage",
                    configuration={
                        "location": regiao.upper(),
                        "storage_class": "STANDARD",
                        "estimated_gb": int(gb),
                        "size_source": "medido" if gb_medido is not None else "estimado",
                    },
                )
            )
        elif volumes > 1:
            # Volumes medidos: N discos do tamanho médio, não um disco gigante.
            nome = nomeador.gerar("disco", categoria)
            por_volume = min(Decimal(math.ceil(gb / volumes)), _GB_MAXIMO_DISCO)
            plano.recursos.append(
                RecursoDerivado(
                    logical_name=nome,
                    resource_type="compute",
                    configuration={
                        "disk_only": True,
                        "size_gb": int(max(por_volume, Decimal("1"))),
                        "volumes": volumes,
                        "size_source": "medido",
                        "disk_type": TIPO_DE_DISCO[categoria],
                        "source_category": categoria,
                    },
                )
            )
        else:
            nome = nomeador.gerar("disco", categoria)
            if gb > _GB_MAXIMO_DISCO:
                plano.avisos.append(
                    f"O total de {categoria} estimado ({int(gb):,} GB) excede o "
                    f"máximo de um disco no destino ({int(_GB_MAXIMO_DISCO):,} GB). "
                    "O recurso saiu no limite; distribua o restante entre volumes "
                    "adicionais conforme o uso real."
                )
                gb = _GB_MAXIMO_DISCO
            plano.recursos.append(
                RecursoDerivado(
                    logical_name=nome,
                    resource_type="compute",
                    configuration={
                        "disk_only": True,
                        "size_gb": int(gb),
                        "size_source": "medido" if gb_medido is not None else "estimado",
                        "disk_type": TIPO_DE_DISCO[categoria],
                        "source_category": categoria,
                    },
                )
            )

        plano.premissas.append(
            _premissa_armazenamento(
                nome,
                categoria,
                origem,
                gb=gb,
                custo=custo,
                taxa=taxa,
                volumes=volumes,
                medido=gb_medido is not None,
            )
        )


def _registrar_snapshots(medido: dict[str, Any], plano: PlanoDerivado) -> None:
    fato = medido.get("snapshots") or {}
    gb = _decimal(fato.get("gb"))
    if gb is None:
        return
    plano.nao_suportado.append(
        f"Snapshots ({int(gb):,} GB em {int(fato.get('recursos') or 0)} recursos, medidos) "
        "não viram disco: são backup. Recrie a política de snapshot no destino."
    )


def _tamanho_armazenamento(
    fato: dict[str, Any],
    custo: Decimal,
    taxas_origem: dict[str, str] | None,
    categoria: str,
) -> tuple[Decimal, Decimal | None, int, Decimal | None] | None:
    """(GB, taxa usada, volumes, GB medido) — ou None se não há o que declarar.

    RN10: GB medido no billing (soma de GB-mês) prevalece sobre custo ÷ taxa.
    Um disco medido pequeno é real e fica; um estimado pequeno é ruído de
    arredondamento da divisão e sai (`_GB_MINIMO`).
    """
    gb_medido = _decimal(fato.get("gb"))
    if gb_medido is not None:
        gb = gb_medido.quantize(Decimal("1"))
        return (gb, None, int(fato.get("recursos") or 0), gb_medido) if gb >= 1 else None
    if taxas_origem is None:
        return None
    taxa = Decimal(taxas_origem[categoria])
    gb = (custo / taxa).quantize(Decimal("1"))
    return (gb, taxa, 0, None) if gb >= _GB_MINIMO else None


def _premissa_armazenamento(
    nome: str,
    categoria: str,
    origem: str,
    *,
    gb: Decimal,
    custo: Decimal,
    taxa: Decimal | None,
    volumes: int,
    medido: bool,
) -> Premissa:
    if medido:
        return Premissa(
            campo=f"{nome}.size_gb",
            categoria="derivacao",
            valor=f"{int(gb)} GB" + (f" em {volumes} volumes" if volumes > 1 else ""),
            base=(
                f"Soma de GB-mês de {categoria} medida no billing da origem"
                + (f" ({volumes} recursos)" if volumes else "")
                + ". Num mês inteiro é o tamanho médio provisionado; GB e GiB "
                "tratados como equivalentes (~7%)."
            ),
        )
    return Premissa(
        campo=f"{nome}.size_gb",
        categoria="estimativa",
        valor=f"{int(gb)} GB",
        base=(
            f"${custo} por mês ÷ ${taxa}/GB-mês (taxa de {categoria} em "
            f"{origem.upper()}). Assume a tier padrão para o balde "
            "inteiro; volume com tier fria sai superdimensionado."
        ),
    )


# ── banco ───────────────────────────────────────────────────────────────


def _derivar_banco(
    payload: dict[str, Any],
    destino: str,
    plano: PlanoDerivado,
    alta_disponibilidade: bool,
    nomeador: Nomeador,
) -> None:
    """vCPU de banco por razão de taxa. Engine e versão ninguém sabe.

    Com export detalhado, o banco principal sai do inventário medido: classe,
    motor e multi-AZ são fatos. Só a versão continua sem base.
    """
    auxiliares = payload.get("custos_auxiliares") or {}
    custo = _decimal(auxiliares.get("banco_dados"))
    if custo is None or custo <= 0:
        return
    if _banco_medido(payload, destino, plano, alta_disponibilidade, nomeador):
        return

    origem = str(payload.get("provedor_origem") or "").lower()
    taxa = _TAXA_DB_VCPU_MES.get(origem)
    if taxa is None:
        plano.nao_suportado.append(
            "Banco gerenciado: sem taxa de referência para o provedor de origem."
        )
        return

    vcpus = max(1, int(custo / Decimal(taxa)))
    nome = nomeador.gerar("banco", "principal")

    plano.recursos.append(
        RecursoDerivado(
            logical_name=nome,
            resource_type="managed_database",
            configuration={
                "vcpus": vcpus,
                "high_availability": alta_disponibilidade,
                "engine_var": "database_engine",
                "version_var": "database_version",
            },
            depends_on=("rede_principal",),
        )
    )
    plano.premissas.append(
        Premissa(
            campo=f"{nome}.vcpus",
            categoria="estimativa",
            valor=str(vcpus),
            base=(
                f"${custo} por mês ÷ ${taxa}/vCPU-mês ({origem.upper()}). O custo "
                "de banco gerenciado inclui instância, storage e I/O, então o "
                "vCPU derivado é direcional — compute domina, mas não é tudo."
            ),
        )
    )
    plano.premissas.append(
        Premissa(
            campo=f"{nome}.engine",
            categoria="sem_base",
            valor=None,
            base=(
                "O billing informa custo de banco, não o motor nem a versão. "
                "Preencha `database_engine` e `database_version` antes de aplicar."
            ),
        )
    )


def familia_do_motor(motor: object) -> str | None:
    """`Aurora PostgreSQL` → `postgresql`: a família decide compatibilidade e a
    variável de versão. Aurora vira o motor base — o destino não tem Aurora."""
    texto = str(motor or "").casefold()
    for chave, familia in _FAMILIAS_DE_MOTOR:
        if chave in texto:
            return familia
    return None


def _banco_medido(
    payload: dict[str, Any],
    destino: str,
    plano: PlanoDerivado,
    alta_disponibilidade: bool,
    nomeador: Nomeador,
) -> bool:
    """Um recurso por grupo medido (classe, motor, multi-AZ), com a contagem.

    Um banco "principal" só enganava: num bill com 165 instâncias de banco, o
    pacote gerava uma. Os grupos mais caros viram recursos com ``instances``;
    o resto é listado. Cache (Redis, Memcached) é outro produto e fica fora.
    """
    inventario = payload.get("inventario_medido") or {}
    grupos = inventario.get("bancos") or []
    relacionais = [
        g for g in grupos if str(g.get("motor") or "").casefold() not in _MOTORES_DE_CACHE
    ]
    caches = [g for g in grupos if g not in relacionais]
    if caches:
        nos = sum(int(g.get("quantidade") or 1) for g in caches)
        plano.nao_suportado.append(
            f"Cache gerenciado ({nos} nós medidos: "
            + ", ".join(f"{g.get('quantidade')}x {g.get('classe')}" for g in caches[:3])
            + ") fora do MVP: exige escolher o produto de cache do destino."
        )
    aceitos = _MOTORES_POR_DESTINO.get(destino)
    gerados = 0
    multi_az: list[str] = []
    incompativeis: list[dict[str, Any]] = []
    restantes: list[dict[str, Any]] = []
    for g in relacionais:
        classe = str(g.get("classe") or "")
        forma = parse_instance_shape(classe)
        familia = familia_do_motor(g.get("motor"))
        if forma is None:
            restantes.append(g)
            continue
        if aceitos is not None and familia not in aceitos:
            incompativeis.append(g)
            continue
        if gerados >= _MAX_GRUPOS_BANCO:
            restantes.append(g)
            continue
        _banco_do_grupo(
            g,
            classe=classe,
            vcpus=int(forma.vcpus),
            familia=familia,
            plano=plano,
            alta_disponibilidade=alta_disponibilidade,
            nomeador=nomeador,
        )
        gerados += 1
        if g.get("multi_az") and not alta_disponibilidade:
            multi_az.append(classe)

    if gerados == 0:
        return False
    if multi_az:
        plano.avisos.append(
            "Bancos Multi-AZ na origem (" + ", ".join(multi_az[:_MAX_LISTADOS]) + ") saíram "
            "sem alta disponibilidade porque a requisição pediu assim."
        )
    if restantes:
        total = sum(int(g.get("quantidade") or 1) for g in restantes)
        plano.avisos.append(
            f"Outros {len(restantes)} grupos de banco medidos ({total} instâncias) não "
            "viraram recurso: "
            + ", ".join(
                f"{g.get('quantidade')}x {g.get('classe')} ({g.get('motor')})"
                for g in restantes[:_MAX_LISTADOS]
            )
            + ("…" if len(restantes) > _MAX_LISTADOS else "")
            + "."
        )
    if incompativeis:
        plano.nao_suportado.append(
            f"Banco em {destino.upper()} só é gerado para "
            + "/".join(sorted(aceitos or ()))
            + "; ficaram de fora: "
            + ", ".join(
                f"{g.get('quantidade')}x {g.get('classe')} ({g.get('motor')})"
                for g in incompativeis[:_MAX_LISTADOS]
            )
            + "."
        )
    detalhados = sum(int(g.get("quantidade") or 1) for g in grupos)
    total = int(inventario.get("total_bancos") or detalhados)
    if total > detalhados:
        plano.avisos.append(
            f"{total - detalhados} instâncias de banco em grupos menores não aparecem "
            "no resumo da análise e não viraram recurso."
        )
    return True


def _banco_do_grupo(
    g: dict[str, Any],
    *,
    classe: str,
    vcpus: int,
    familia: str | None,
    plano: PlanoDerivado,
    alta_disponibilidade: bool,
    nomeador: Nomeador,
) -> None:
    quantidade = int(g.get("quantidade") or 1)
    motor = g.get("motor")
    nome = nomeador.gerar("banco", familia or "motor", classe)
    plano.recursos.append(
        RecursoDerivado(
            logical_name=nome,
            resource_type="managed_database",
            configuration={
                "vcpus": vcpus,
                "instances": quantidade,
                "high_availability": alta_disponibilidade,
                "engine_var": "database_engine",
                "version_var": f"database_version_{familia}" if familia else "database_version",
                "vcpu_source": "medido",
                "source_class": classe,
                "engine_measured": motor,
                "engine_family": familia,
                "instances_measured": quantidade,
                # Chave do grupo medido: é por ela que a topologia acha a versão
                # e a rede. Não é renderizada.
                "source_multi_az": bool(g.get("multi_az")),
                "source_region": g.get("regiao"),
            },
            depends_on=("rede_principal",),
        )
    )
    plano.premissas.append(
        Premissa(
            campo=f"{nome}.vcpus",
            categoria="derivacao",
            valor=f"{vcpus} x{quantidade}",
            base=(
                f"Classe medida na origem: {classe} ({quantidade} instância(s), motor "
                f"{motor or 'não informado'}); {vcpus} vCPU pela especificação da classe."
            ),
        )
    )
    plano.premissas.append(
        Premissa(
            campo=f"{nome}.engine",
            categoria="derivacao" if motor else "sem_base",
            valor=str(motor) if motor else None,
            base=(
                f"Motor medido no billing: {motor}. A versão não aparece no billing: "
                "preencha a variável de versão antes de aplicar."
                if motor
                else "O billing não informou o motor. Preencha `database_engine` e "
                "`database_version` antes de aplicar."
            ),
        )
    )


# ── lacunas ─────────────────────────────────────────────────────────────


def _familia_app_service(pool: dict[str, Any]) -> bool:
    return str(pool.get("familia") or "").upper().startswith("APPSERVICE_")


def _registrar_lacunas(payload: dict[str, Any], plano: PlanoDerivado) -> None:
    """O que o billing não contém, dito em voz alta.

    Egresso e compromisso aparecem como custo e não viram recurso: egresso é
    consequência de tráfego, não coisa a provisionar, e reserva é instrumento
    comercial. Silenciar os dois faria o `.tf` parecer cobrir um gasto que ele
    não cobre.
    """
    auxiliares = payload.get("custos_auxiliares") or {}

    if _decimal(auxiliares.get("egresso_rede")):
        plano.nao_suportado.append(
            "Egresso de rede aparece no custo mas não vira recurso: é "
            "consequência do tráfego, não algo a provisionar."
        )
    if any(_decimal(auxiliares.get(chave)) for chave in _REDE_PAGA):
        plano.nao_suportado.append(
            "Serviços de rede pagos (balanceador, NAT, VPN, IP público, tráfego entre "
            "zonas) aparecem no custo e não viram recurso: a rede do pacote vem das "
            "escolhas da requisição."
        )
    if _decimal(auxiliares.get("compromisso_reserva")):
        plano.nao_suportado.append(
            "Compromisso/reserva (Savings Plan, CUD) é instrumento comercial, "
            "contratado fora do Terraform."
        )
    # Cluster visto pelo inventário (topologia congelada) ou pelo custo do plano de
    # controle no billing. As aplicações de dentro dele não são separadas: o
    # inventário da nuvem não vê Deployment nem Service (PLANO_CENARIOS_DE_ENTRADA §9,
    # opção B adiada pelo usuário em 17/10)
    contagens = ((payload.get("topologia") or {}).get("resumo") or {}).get("contagens") or {}
    clusters = (contagens.get("por_tipo") or {}).get("cluster_kubernetes")
    if (
        _decimal(clusters)
        or _decimal(auxiliares.get("gke_plano_controle"))
        or _decimal(auxiliares.get("kubernetes_outros"))
    ):
        plano.nao_suportado.append(
            "Kubernetes: este pacote é da análise inteira e leva os nós do cluster como "
            "máquinas virtuais. O cluster gerenciado do destino, com os grupos de nós, sai "
            "no pacote gerado pela aba Migração (plano de migração, com o inventário "
            "anexado). As aplicações de dentro do cluster não viajam pelo Terraform: o "
            "inventário da nuvem não vê Deployment nem Service."
        )
    pools = [p for p in payload.get("pools_computacao") or [] if isinstance(p, dict)]
    if any(_familia_app_service(p) for p in pools):
        plano.nao_suportado.append(
            "O plano do App Service entra como máquinas do mesmo tamanho. Para manter a "
            "aplicação em PaaS no destino (Elastic Beanstalk, App Engine, Container "
            "Instances da OCI), crie o serviço à parte e descarte essas máquinas."
        )
    if _decimal(auxiliares.get("armazenamento_arquivos")) or _decimal(
        auxiliares.get("arquivos_capacidade")
    ):
        # T4: o Filestore saía como disco HDD e virava ~1.570 GB de disco que não existe
        plano.nao_suportado.append(
            "Armazenamento de arquivos (Filestore, EFS, Azure Files, File Storage da OCI) "
            "aparece no custo e não vira disco: o pacote não cria compartilhamento NFS. "
            "Crie o serviço de arquivos do destino à parte e monte nas máquinas."
        )
    if any(_decimal(auxiliares.get(CHAVE_POR_USO[c])) for c in OBSERVABILIDADE_E_BORDA):
        plano.nao_suportado.append(
            "Logs, DNS, WAF, registro de imagens e métricas aparecem no custo e não viram "
            "recurso: crie no destino a retenção de log, as zonas de DNS, as regras do WAF e "
            "o repositório de imagens."
        )
    if any(_decimal(auxiliares.get(CHAVE_POR_USO[c])) for c in BASTION_SEGURANCA_E_SERVERLESS):
        plano.nao_suportado.append(
            "Contêiner serverless (Container Apps, Cloud Run), bastion e verificação de "
            "segurança aparecem no custo e não viram recurso: crie no destino o serviço de "
            "contêiner, o acesso às máquinas sem IP público e a verificação de imagens."
        )
    reconhecidos = (auxiliares.get("por_servico") or {}).get("reconhecidos_sem_cotacao") or {}
    if _decimal(auxiliares.get("reconhecidos_sem_cotacao")):
        nomes = ", ".join(sorted(reconhecidos)[:6]) or "serviços gerenciados"
        plano.nao_suportado.append(
            f"Serviços sem equivalente cotado ({nomes}) aparecem no custo e não viram "
            "recurso: crie o serviço do destino à parte."
        )
    nao_classificado = auxiliares.get("nao_classificado") or {}
    if _decimal(nao_classificado.get("custo")):
        plano.nao_suportado.append(
            f"{nao_classificado.get('quantidade_skus', '?')} SKUs não "
            "classificados não entraram no pacote."
        )

    plano.premissas.append(
        Premissa(
            campo="rede",
            categoria="escolha_usuario",
            valor=None,
            base=(
                "O billing não contém topologia de rede. VPC, subnet e faixa de "
                "IP vêm inteiramente das escolhas informadas na requisição."
            ),
        )
    )


# ── utilidades ──────────────────────────────────────────────────────────


class Nomeador:
    """Gera nomes de recurso válidos e **únicos**.

    A unicidade não é detalhe: dois blocos com o mesmo nome fazem o Terraform
    recusar o arquivo inteiro com "Duplicate resource". E a colisão é provável,
    não teórica — o pool é chaveado por (família, região), então a mesma família
    em duas regiões chega aqui com o mesmo `familia`. Só isso já bastaria.

    Casing também colide: `M6I` e `m6i` são famílias distintas na origem e o
    mesmo identificador em HCL, que é case-sensitive mas não distingue depois do
    saneamento para minúsculas.
    """

    def __init__(self, usados: Iterable[str] = ()) -> None:
        # Semeado com os nomes que já existem quando a topologia acrescenta
        # recursos a um plano pronto.
        self._usados: set[str] = set(usados)

    def gerar(self, prefixo: str, *partes: object) -> str:
        base = "_".join(limpo for limpo in (_sanear(p) for p in partes if p) if limpo) or "recurso"
        nome = f"{prefixo}_{base}"[:57]

        if nome not in self._usados:
            self._usados.add(nome)
            return nome

        # Sufixo numérico em vez de erro: um pool a mais não pode impedir a
        # geração inteira, e o README carrega a família de origem de cada um.
        for n in range(2, 1000):
            candidato = f"{nome}_{n}"
            if candidato not in self._usados:
                self._usados.add(candidato)
                return candidato
        raise ValueError(f"não foi possível gerar nome único para {nome!r}")


def _sanear(bruto: object) -> str:
    """Só ASCII alfanumérico.

    `"ç".isalnum()` é `True` em Python, então acento sobrevivia ao saneamento e
    só explodia na validação do identificador HCL, que é ASCII. Um nome de
    família com acento derrubava a geração inteira.
    """
    limpo = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in str(bruto).lower())
    return "_".join(p for p in limpo.split("_") if p)


def _decimal(valor: object) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d > 0 else None
