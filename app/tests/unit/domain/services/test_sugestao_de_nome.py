from app.domain.services.sugestao_de_nome import SEMELHANCA_MINIMA, semelhanca, sugerir

_AWS = [
    "Amazon MSK",
    "Amazon Managed Streaming for Apache Kafka (MSK)",
    "Amazon Simple Queue Service (SQS)",
    "Amazon Elastic Container Registry (ECR)",
    "AWS Key Management Service (KMS)",
    "Amazon CloudFront",
]


def test_sigla_do_de_para_casa_com_as_iniciais_do_nome_da_fatura() -> None:
    assert semelhanca("Simple Queue Service", "Amazon Simple Queue Service (SQS)") >= 0.9
    assert semelhanca("Key Management Service", "AWS Key Management Service (KMS)") >= 0.9


def test_nome_parecido_sugere_a_linha_do_de_para() -> None:
    sugestao = sugerir("Amazon Managed Streaming for Apache Kafka", _AWS)
    assert sugestao is not None
    assert sugestao.alvo == "Amazon Managed Streaming for Apache Kafka (MSK)"


def test_nome_sem_parente_nao_sugere() -> None:
    assert sugerir("Amazon Q Developer", _AWS) is None


def test_palavras_genericas_nao_contam() -> None:
    # "Amazon" e "Service" em comum não fazem dois serviços parecidos
    assert semelhanca("Amazon Bedrock Service", "Amazon Simple Queue Service (SQS)") < (
        SEMELHANCA_MINIMA
    )


def test_nome_vazio() -> None:
    assert semelhanca("", "Amazon MSK") == 0.0
    assert sugerir("Cloud", _AWS) is None
