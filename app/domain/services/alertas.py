"""Alertas que o **backend** levanta contra os próprios insumos.

O plano v2 deu ao agente o direito de contestar um número nosso: ele sinaliza,
a confiança cai, o valor não muda (`agent_result.processar_sugestoes`). Faltava
o simétrico — o backend contestando a si mesmo quando *sabe* que um insumo é
frágil.

O caso que motivou o módulo: `_resolve_fx` devolvia taxa 1 rotulada
`SEM_TAXA_CAMBIO` para qualquer par sem cotação, e esse rótulo morria no ledger.
`grep -rn SEM_TAXA_CAMBIO app/` retornava uma linha só — a que criava o rótulo.
Ninguém lia. Na prática um bill em euro era somado com dólar como se fossem a
mesma moeda, e o resultado saía **sem nenhum aviso** — pior que falhar, porque
o número sai plausível.

Alertas saem no mesmo formato das sugestões do agente e vão para a mesma lista
na tela. Quem lê não precisa saber quem levantou: precisa saber que existe uma
dúvida e qual.

Módulo puro — entra dataclass, sai dataclass. Sem I/O, sem framework.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.domain.services.currency import FxRate

ORIGEM_BACKEND = "backend"

# Acima disto a cotação deixa de ser "de hoje" e vira premissa a declarar. BRL
# se move ~1-2% ao mês; um mês de defasagem ainda descreve a realidade, três não.
_DIAS_ENVELHECIDA = 30
_DIAS_VENCIDA = 90


@dataclass(frozen=True, slots=True)
class Alerta:
    """Uma dúvida do backend sobre um insumo próprio.

    `degraus_confianca` é o que separa "avisar" de "punir". Zero significa que o
    alerta aparece na tela mas não mexe no nível — é o default, porque rebaixar
    a confiança de toda análise por um aviso rotineiro tornaria o nível inútil
    justamente onde ele precisa discriminar.
    """

    tipo: str
    severidade: str
    titulo: str
    detalhe: str
    referencia: str | None = None
    degraus_confianca: int = 0

    @property
    def rebaixa(self) -> bool:
        return self.degraus_confianca > 0

    def as_sugestao(self) -> dict[str, object]:
        """Mesmo formato de `SugestaoOut` — vai na lista que a tela exibe.

        `efeito_backend` já vem preenchido: o alerta é do backend, então o
        efeito não é uma promessa a cumprir depois, é o que de fato foi aplicado
        ao montar o payload.
        """
        return {
            "tipo": self.tipo,
            "severidade": self.severidade,
            "titulo": self.titulo,
            "detalhe": self.detalhe,
            "referencia": self.referencia,
            "origem": ORIGEM_BACKEND,
            "efeito_backend": ("confianca_rebaixada" if self.rebaixa else "informativo"),
        }


def avaliar_cambio(fx: FxRate, hoje: date) -> Alerta | None:
    """Alerta sobre a taxa aplicada, ou ``None`` quando ela está saudável.

    Quatro estados, em ordem de gravidade:

    1. **sem taxa** — a conversão foi identidade entre moedas diferentes. O
       número está errado, não impreciso: rebaixa.
    2. **vencida** — cotação com mais de 90 dias. Rebaixa.
    3. **sem data** — o fallback de ambiente. O valor é plausível e ninguém sabe
       de quando é; avisa sem rebaixar, porque é o estado normal hoje e punir o
       normal esvazia o sinal.
    4. **envelhecida** — mais de 30 dias. Avisa.

    A identidade legítima (origem já em USD) não gera alerta: não houve
    conversão a questionar.
    """
    origem, _, destino = fx.par.partition("/")
    if origem == destino:
        return None  # não houve conversão a questionar

    if fx.sem_taxa:
        return Alerta(
            tipo="contestacao_preco",
            severidade="alta",
            titulo=f"Sem taxa de câmbio para {fx.par}",
            detalhe=(
                f"Não há cotação de {fx.par} na tabela nem no ambiente. Os valores "
                "foram somados sem conversão, ou seja, comparando moedas "
                "diferentes como se fossem a mesma. Trate a economia estimada "
                "como não confiável até cadastrar a taxa."
            ),
            referencia=fx.par,
            degraus_confianca=1,
        )

    idade = fx.idade_em_dias(hoje)

    if idade is None:
        return Alerta(
            tipo="dado_faltante",
            severidade="media",
            titulo=f"Câmbio de {fx.par} sem data de cotação",
            detalhe=(
                f"A taxa {fx.taxa} veio de '{fx.fonte}' e não tem data — não há "
                "como saber se descreve o câmbio de hoje. Os valores convertidos "
                "carregam o erro da taxa, qualquer que seja ele."
            ),
            referencia=fx.par,
        )

    if idade > _DIAS_VENCIDA:
        return Alerta(
            tipo="contestacao_preco",
            severidade="alta",
            titulo=f"Câmbio de {fx.par} desatualizado ({idade} dias)",
            detalhe=(
                f"A cotação é de {fx.data} — {idade} dias atrás. Acima de "
                f"{_DIAS_VENCIDA} dias a taxa deixa de descrever o câmbio atual e "
                "a conversão distorce a comparação entre provedores."
            ),
            referencia=fx.par,
            degraus_confianca=1,
        )

    if idade > _DIAS_ENVELHECIDA:
        return Alerta(
            tipo="dado_faltante",
            severidade="media",
            titulo=f"Câmbio de {fx.par} com {idade} dias",
            detalhe=(
                f"A cotação é de {fx.data}. Ainda utilizável, mas vale "
                "ressincronizar antes de decidir migração com base nesta análise."
            ),
            referencia=fx.par,
        )

    return None


def avaliar_regioes(
    *,
    sem_zona: Sequence[str],
    zona_padrao: str,
    aproximadas: Sequence[Mapping[str, str]],
) -> list[Alerta]:
    """Alertas da cotação regional (PLANO_PRECO_REGIONAL D5 e D6).

    Nenhum rebaixa por si: o preço de outra região já entra como `reference`, e
    a confiança da análise cai pela razão de custo oficial. Rebaixar de novo
    contaria o mesmo problema duas vezes.

    - **região de origem desconhecida**: o pool foi cotado na zona padrão. Num
      bill de São Paulo isso deixa a economia de sair ~60% otimista;
    - **preço de outra região**: o destino não tinha preço sincronizado na
      região equivalente. O detalhe diz qual sync roda para corrigir.
    """
    alertas: list[Alerta] = []
    if sem_zona:
        alertas.append(
            Alerta(
                tipo="dado_faltante",
                severidade="media",
                titulo="Região de origem não identificada",
                detalhe=(
                    f"{len(sem_zona)} pool(s) sem região que a tabela de zonas "
                    f"reconheça ({', '.join(sem_zona[:5])}). Os destinos foram "
                    f"cotados na zona padrão ({zona_padrao}). Se a carga roda em "
                    "outra metrópole, a diferença de preço regional não está na conta."
                ),
                referencia=", ".join(sem_zona[:5]),
            )
        )
    if aproximadas:
        pares = sorted({f"{a['provedor']} {a['pedida']} → {a['cotada']}" for a in aproximadas})
        alertas.append(
            Alerta(
                tipo="dado_faltante",
                severidade="media",
                titulo="Preço de outra região na cotação",
                detalhe=(
                    "Sem preço sincronizado na região equivalente; a cotação usou "
                    f"outra: {'; '.join(pares[:6])}. Esses valores entram como "
                    "'reference'. Para corrigir, rode `scripts/sync_prices.py` com "
                    "a zona da carga em `--zonas`."
                ),
                referencia="; ".join(pares[:6]),
            )
        )
    return alertas
