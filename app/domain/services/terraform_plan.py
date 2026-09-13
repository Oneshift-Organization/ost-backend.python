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

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Taxas de referência por GB-mês, usadas para voltar de dinheiro para tamanho.
# São as mesmas de `seed_category_rates`; ficam aqui porque a derivação é pura e
# não consulta banco. Divergir delas não quebra nada — muda o GB estimado, que
# já está declarado como estimativa.
_TAXA_GB_MES: dict[str, dict[str, str]] = {
    "aws": {"storage_ssd": "0.080", "storage_hdd": "0.045", "object_storage": "0.023"},
    "gcp": {"storage_ssd": "0.170", "storage_hdd": "0.040", "object_storage": "0.020"},
    "azure": {"storage_ssd": "0.088", "storage_hdd": "0.045", "object_storage": "0.018"},
    "oci": {"storage_ssd": "0.0255", "storage_hdd": "0.0255", "object_storage": "0.0255"},
}

# $/vCPU-mês de banco gerenciado, general-purpose. Mesma origem.
_TAXA_DB_VCPU_MES: dict[str, str] = {
    "aws": "62", "gcp": "60", "azure": "73", "oci": "30",
}

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

# Confiança do preço que sustenta a escolha do shape. Só `official` é preço
# coletado da API do provedor; `reference` é taxa publicada e `low` é catálogo
# interno. O packer escolhe a máquina COMPARANDO PREÇOS — se o preço é fraco, a
# escolha herda a fraqueza, e chamá-la de "calculada" seria afirmar mais do que
# sabemos.
_CONFIANCA_OFICIAL = "official"

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
    nomeador = _Nomeador()

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
    nomeador: _Nomeador,
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

        sku = equivalente.get("sku")
        # A região entra no nome porque o pool é chaveado por (família, região):
        # sem ela, a mesma família em duas regiões vira o mesmo identificador.
        nome = nomeador.gerar("vm", familia, pool.get("regiao"))

        if not sku:
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
        if pool.get("custo_coberto_por_compromisso"):
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


def _equivalente_do_destino(
    equivalentes: list[dict[str, Any]], destino: str
) -> dict[str, Any] | None:
    """Prefere equivalente com shape ao curado, quando há os dois.

    Sem isto, a ordem da lista decidiria — e um equivalente curado apagaria um
    com hardware casado só por vir antes.
    """
    candidatos = [
        e for e in equivalentes
        if str(e.get("provedor", "")).lower() == destino
    ]
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
    nomeador: _Nomeador,
) -> None:
    """Volta de dinheiro para tamanho: ``GB = custo ÷ taxa``.

    É estimativa, não conta: assume que o balde inteiro está na tier padrão. Um
    bill com muito Glacier ou Nearline sai superdimensionado, porque a taxa fria
    é bem menor. Por isso cada volume emite a premissa com a taxa usada.
    """
    auxiliares = payload.get("custos_auxiliares") or {}
    origem = str(payload.get("provedor_origem") or "").lower()
    taxas_origem = _TAXA_GB_MES.get(origem)

    if taxas_origem is None:
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

        taxa = Decimal(taxas_origem[categoria])
        gb = (custo / taxa).quantize(Decimal("1"))

        if gb < _GB_MINIMO:
            continue

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
                        "disk_type": (
                            "pd-ssd" if categoria == "storage_ssd" else "pd-standard"
                        ),
                    },
                )
            )

        plano.premissas.append(
            Premissa(
                campo=f"{nome}.size_gb",
                categoria="estimativa",
                valor=f"{int(gb)} GB",
                base=(
                    f"${custo} por mês ÷ ${taxa}/GB-mês (taxa de {categoria} em "
                    f"{origem.upper()}). Assume a tier padrão para o balde "
                    "inteiro; volume com tier fria sai superdimensionado."
                ),
            )
        )


# ── banco ───────────────────────────────────────────────────────────────


def _derivar_banco(
    payload: dict[str, Any],
    destino: str,
    plano: PlanoDerivado,
    alta_disponibilidade: bool,
    nomeador: _Nomeador,
) -> None:
    """vCPU de banco por razão de taxa. Engine e versão ninguém sabe."""
    auxiliares = payload.get("custos_auxiliares") or {}
    custo = _decimal(auxiliares.get("banco_dados"))
    if custo is None or custo <= 0:
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


# ── lacunas ─────────────────────────────────────────────────────────────


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
    if _decimal(auxiliares.get("compromisso_reserva")):
        plano.nao_suportado.append(
            "Compromisso/reserva (Savings Plan, CUD) é instrumento comercial, "
            "contratado fora do Terraform."
        )
    if _decimal(auxiliares.get("gke_plano_controle")):
        plano.nao_suportado.append(
            "Plano de controle de Kubernetes fora do MVP: exige decidir sobre "
            "node pools, versão e rede do cluster."
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


class _Nomeador:
    """Gera nomes de recurso válidos e **únicos**.

    A unicidade não é detalhe: dois blocos com o mesmo nome fazem o Terraform
    recusar o arquivo inteiro com "Duplicate resource". E a colisão é provável,
    não teórica — o pool é chaveado por (família, região), então a mesma família
    em duas regiões chega aqui com o mesmo `familia`. Só isso já bastaria.

    Casing também colide: `M6I` e `m6i` são famílias distintas na origem e o
    mesmo identificador em HCL, que é case-sensitive mas não distingue depois do
    saneamento para minúsculas.
    """

    def __init__(self) -> None:
        self._usados: set[str] = set()

    def gerar(self, prefixo: str, *partes: object) -> str:
        base = "_".join(
            limpo for limpo in (_sanear(p) for p in partes if p) if limpo
        ) or "recurso"
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
    limpo = "".join(
        c if (c.isascii() and c.isalnum()) else "_" for c in str(bruto).lower()
    )
    return "_".join(p for p in limpo.split("_") if p)


def _decimal(valor: object) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d > 0 else None
