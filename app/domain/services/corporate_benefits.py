"""Reviewed public sources. No live browsing, credentials, eligibility inference or LLM.

Version/date describe this catalog, not the customer's analysis date. No advertised
credit maxima are treated as approved amounts. Changes require source review and a
catalog + agent authority version bump; old persisted analyses remain unchanged.
"""

CATALOG_VERSION = "corporate-benefits-v2"
REVIEWED_ON = "2026-10-18"

_CREDITS = (
    (
        "aws",
        "AWS Activate",
        "Créditos para startups sujeitas às regras do programa.",
        "https://aws.amazon.com/startups/credits/",
        [
            "Perfil e estágio da startup; histórico de créditos recebidos.",
            "Modalidade aplicável, vínculo com parceiro quando exigido e aprovação da AWS.",
        ],
    ),
    (
        "azure",
        "Microsoft for Startups",
        "Créditos e recursos para startups; benefícios dependem da elegibilidade e evolução.",
        "https://learn.microsoft.com/en-us/startups/microsoft-for-startups/overview",
        [
            "Produto próprio de software, perfil da empresa e estágio de investimento.",
            "País atendido, histórico de créditos e aprovação pela Microsoft.",
        ],
    ),
    (
        "gcp",
        "Google for Startups Cloud Program",
        "Créditos e orientação para startups conforme o estágio e a modalidade aprovada.",
        "https://cloud.google.com/startup/benefits",
        [
            "Data de fundação, investimento e histórico de créditos.",
            "Modalidade, serviços elegíveis, prazo e aprovação pelo Google Cloud.",
        ],
    ),
    (
        "oci",
        "Oracle Startup Growth Engine",
        "Startups selecionadas podem receber até US$ 60 mil em créditos OCI ou "
        "financiamento para avaliações técnicas e de negócio, conforme a modalidade. "
        "Não é saldo aprovado nem desconto permanente.",
        "https://www.oracle.com/br/artificial-intelligence/startup-growth-engine/",
        [
            "Produto tecnológico validado com clientes, potencial de escala e workloads "
            "compatíveis com OCI; disposição para desenvolver ou migrar para a plataforma.",
            "Ausência de relação comercial ativa com OCI no momento da inscrição.",
            "País atendido, seleção e nível elegível do Oracle PartnerNetwork (eOPN); "
            "confirmar a modalidade, o valor, a validade e os serviços autorizados.",
        ],
    ),
)

_SUPPORT = (
    ("aws", "AWS Support", "https://aws.amazon.com/premiumsupport/plans/"),
    ("azure", "Azure Support", "https://azure.microsoft.com/en-us/support/plans/"),
    ("gcp", "Google Cloud Customer Care", "https://cloud.google.com/support"),
    (
        "oci",
        "Oracle Support para infraestrutura cloud",
        "https://www.oracle.com/support/",
    ),
)


def build_corporate_benefits() -> dict:
    """Fresh models per analysis; callers cannot mutate the catalog for other users."""
    opportunities = [
        dict(
            id=f"{provider}-credits",
            provider=provider,
            kind="credits",
            title=title,
            summary=summary,
            source_url=url,
            requirements=list(requirements),
            eligibility="not_verified",
        )
        for provider, title, summary, url, requirements in _CREDITS
    ]
    opportunities.extend(
        dict(
            id=f"{provider}-support",
            provider=provider,
            kind="support",
            title=title,
            summary="Conferir cobertura, canais de atendimento e condições do suporte.",
            source_url=url,
            eligibility="not_verified",
            requirements=[
                "Criticidade da aplicação, horário de operação e tempo de resposta necessário.",
                "Plano contratado, serviços cobertos e eventuais custos adicionais.",
            ],
        )
        for provider, title, url in _SUPPORT
    )
    return dict(
        score_effect="none",
        catalog_version=CATALOG_VERSION,
        reviewed_on=REVIEWED_ON,
        opportunities=opportunities,
        limitations=[
            "O billing não comprova elegibilidade. Nenhum benefício foi aprovado pelo OneShift.",
            "Programas podem mudar; reconfirme as condições na fonte oficial.",
            "Créditos potenciais e suporte não alteram o score nem a economia exibida.",
            "Antes de simular créditos aprovados, validar saldo, moeda, vencimento e serviços "
            "cobertos. Comparar também o custo sem benefício após o prazo.",
        ],
    )
