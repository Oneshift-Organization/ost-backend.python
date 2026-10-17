"""Derivação da infraestrutura de destino.

O que estes testes protegem: o `.tf` descreve a análise que o usuário aprovou, e
descreve igual toda vez. Nada aqui passa por modelo — se passasse, duas execuções
poderiam agrupar diferente num artefato que o cliente aplica na infra dele.

E o que foi estimado precisa dizer que foi. Um `.tf` parece preciso; um número
sem premissa ao lado vira fato aos olhos de quem lê.
"""

from __future__ import annotations

from app.domain.services.terraform_plan import derivar_plano
from app.schemas.terraform import _BASE_MAXIMA

# ── payload de referência ───────────────────────────────────────────────


def _payload() -> dict:
    """Forma real do `llm_input_payload` (ver `build_pipeline_llm_input_payload`)."""
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "gasto_total": 48000.0,
        "pools_computacao": [
            {
                "familia": "M6I",
                "capacidade": {"vcpus": 32.0, "ram_gib": 128.0},
                "custo_computacao": 3200.0,
                "equivalentes": [
                    {
                        "provedor": "GCP",
                        "nome_servico": "Compute Engine N2",
                        "sku": "n2-standard-8",
                        "instancias": 4,
                        "correspondencia_tecnica": {"vcpus": 32.0, "ram_gb": 128.0},
                    },
                    {
                        "provedor": "AZURE",
                        "nome_servico": "Azure Virtual Machines",
                        "base_correspondencia": "curated_only",
                    },
                ],
            }
        ],
        "custos_auxiliares": {
            "armazenamento_ssd": 800.0,      # ÷ 0.080 = 10.000 GB
            "armazenamento_objetos": 230.0,  # ÷ 0.023 = 10.000 GB
            "banco_dados": 620.0,            # ÷ 62    = 10 vCPU
            "egresso_rede": 4500.0,
            "compromisso_reserva": 2000.0,
        },
    }


def _derivar(payload: dict | None = None, **kw):
    base = {"provedor_destino": "gcp", "regiao": "southamerica-east1"}
    return derivar_plano(payload if payload is not None else _payload(), **{**base, **kw})


def _recurso(plano, nome: str):
    achados = [r for r in plano.recursos if r.logical_name == nome]
    assert achados, f"{nome} não foi derivado: {[r.logical_name for r in plano.recursos]}"
    return achados[0]


def _premissa(plano, campo: str):
    achadas = [p for p in plano.premissas if p.campo == campo]
    assert achadas, f"sem premissa para {campo}"
    return achadas[0]


# ── compute: derivação pura ─────────────────────────────────────────────


def test_o_shape_vem_do_packer_nao_de_palpite() -> None:
    r = _recurso(_derivar(), "vm_m6i")
    assert r.configuration["machine_type"] == "n2-standard-8"
    assert r.configuration["instances"] == 4


def _com_confianca(nivel: str, origem: str = "cloud_price_points") -> dict:
    payload = _payload()
    payload["pools_computacao"][0]["equivalentes"][0]["financeiro"] = {
        "confianca_preco": nivel,
        "preco_origem": origem,
    }
    return payload


def test_shape_com_preco_oficial_e_derivacao() -> None:
    """`derivacao` significa que existe uma resposta certa e esta é ela. Vale
    quando o preço que sustentou o empacotamento veio da API do provedor."""
    p = _premissa(_derivar(_com_confianca("official")), "vm_m6i.machine_type")
    assert p.categoria == "derivacao"
    assert "n2-standard-8" in (p.valor or "")


def test_shape_com_preco_de_catalogo_e_estimativa() -> None:
    """O packer escolhe a máquina COMPARANDO PREÇOS. Se o preço é catálogo
    interno, a escolha herda a fraqueza — e no bill AWS real isso vale para
    **todos** os 20 pools. Chamar de "Calculado" seria afirmar mais do que
    sabemos, e o cliente aplicaria a máquina achando que era a resposta certa.
    """
    p = _premissa(_derivar(_com_confianca("low", "seed_catalog")), "vm_m6i.machine_type")
    assert p.categoria == "estimativa"
    assert "seed_catalog" in p.base
    assert "não é preço coletado" in p.base


def test_preco_de_referencia_tambem_nao_e_derivacao() -> None:
    """`reference` é taxa publicada pelo provedor — melhor que catálogo, e ainda
    assim não é preço coletado."""
    p = _premissa(_derivar(_com_confianca("reference")), "vm_m6i.machine_type")
    assert p.categoria == "estimativa"


def test_idade_do_preco_entra_na_premissa() -> None:
    """É a idade que torna a proveniência acionável: `official` pode ser de
    ontem ou de três meses."""
    payload = _com_confianca("official")
    payload["pools_computacao"][0]["equivalentes"][0]["financeiro"]["preco_idade_dias"] = 97
    p = _premissa(_derivar(payload), "vm_m6i.machine_type")
    assert "97 dias" in p.base


def test_equivalente_com_shape_ganha_do_curado() -> None:
    """Com os dois presentes, a ordem da lista não pode decidir."""
    payload = _payload()
    equivalentes = payload["pools_computacao"][0]["equivalentes"]
    payload["pools_computacao"][0]["equivalentes"] = [
        {"provedor": "GCP", "nome_servico": "X", "base_correspondencia": "curated_only"},
        *equivalentes,
    ]
    assert _recurso(_derivar(payload), "vm_m6i").configuration["machine_type"]


def test_sem_shape_vira_lacuna_e_nao_maquina_inventada() -> None:
    """Catálogo curado sabe o serviço, não o hardware. Escolher um shape aqui
    seria o modelo do backend inventando — o erro que todo o desenho evita."""
    payload = _payload()
    payload["pools_computacao"][0]["equivalentes"] = [
        {
            "provedor": "GCP",
            "nome_servico": "Compute Engine",
            "base_correspondencia": "curated_only",
        }
    ]
    plano = _derivar(payload)
    assert not [r for r in plano.recursos if "machine_type" in r.configuration]
    assert _premissa(plano, "vm_m6i.machine_type").categoria == "sem_base"


def test_pool_sem_equivalente_no_destino_e_declarado() -> None:
    plano = _derivar(_payload(), provedor_destino="oci")
    assert any("M6I" in item for item in plano.nao_suportado)


# ── armazenamento: de dinheiro para GB ──────────────────────────────────


def test_disco_dimensionado_pela_taxa_da_origem() -> None:
    """$800 ÷ $0,080/GB-mês (SSD na AWS) = 10.000 GB."""
    r = _recurso(_derivar(), "disco_storage_ssd")
    assert r.configuration["size_gb"] == 10_000


def test_bucket_usa_taxa_de_objeto_nao_de_disco() -> None:
    """$230 ÷ $0,023 = 10.000 GB. Com a taxa de bloco daria menos da metade —
    era exatamente o erro da classificação antiga."""
    r = _recurso(_derivar(), "bucket_object_storage")
    assert r.resource_type == "object_storage"
    assert r.configuration["estimated_gb"] == 10_000


def test_tamanho_e_estimativa_com_a_taxa_declarada() -> None:
    p = _premissa(_derivar(), "disco_storage_ssd.size_gb")
    assert p.categoria == "estimativa"
    assert "0.080" in p.base, "a taxa usada tem que estar na premissa"
    assert "tier" in p.base.lower(), "o limite da estimativa tem que estar dito"


def test_valor_irrisorio_nao_vira_volume() -> None:
    """Centavos de storage dividem em poucos GB — ruído de arredondamento. Um
    volume de 2 GB no `.tf` engana mais do que a omissão."""
    payload = _payload()
    payload["custos_auxiliares"] = {"armazenamento_ssd": 0.5}
    plano = _derivar(payload)
    assert not [r for r in plano.recursos if "size_gb" in r.configuration]


def test_origem_sem_taxa_conhecida_nao_inventa_tamanho() -> None:
    payload = _payload()
    payload["provedor_origem"] = "ibm"
    plano = _derivar(payload)
    assert not [r for r in plano.recursos if "size_gb" in r.configuration]
    assert any("taxa de armazenamento" in a for a in plano.avisos)


# ── banco ───────────────────────────────────────────────────────────────


def test_vcpu_de_banco_derivado_da_taxa() -> None:
    """$620 ÷ $62/vCPU-mês = 10 vCPU."""
    assert _recurso(_derivar(), "banco_principal").configuration["vcpus"] == 10


def test_engine_do_banco_nao_e_adivinhado() -> None:
    """O billing informa custo, não motor. Escolher PostgreSQL porque é o mais
    comum seria inventar a infraestrutura do cliente."""
    p = _premissa(_derivar(), "banco_principal.engine")
    assert p.categoria == "sem_base"
    assert p.valor is None


def test_alta_disponibilidade_e_escolha_do_usuario() -> None:
    assert _recurso(_derivar(alta_disponibilidade=True), "banco_principal").configuration[
        "high_availability"
    ]


# ── o que não vira recurso ──────────────────────────────────────────────


def test_egresso_e_compromisso_aparecem_como_nao_suportados() -> None:
    """Ambos são custo real. Calar sobre eles faria o pacote parecer cobrir um
    gasto que ele não cobre."""
    plano = _derivar()
    texto = " ".join(plano.nao_suportado)
    assert "Egresso" in texto
    assert "Compromisso" in texto or "reserva" in texto


def _aviso_do_cluster(plano) -> list[str]:
    return [item for item in plano.nao_suportado if item.startswith("Kubernetes:")]


def test_cluster_do_inventario_avisa_que_vai_inteiro() -> None:
    """Opção B adiada: as aplicações de dentro do cluster não são separadas."""
    payload = _payload()
    payload["topologia"] = {"resumo": {"contagens": {"por_tipo": {"cluster_kubernetes": 1}}}}
    [aviso] = _aviso_do_cluster(_derivar(payload))
    assert "cluster inteiro" in aviso
    assert "Deployment" in aviso


def test_plano_de_controle_no_billing_tambem_avisa() -> None:
    for chave in ("gke_plano_controle", "kubernetes_outros"):
        payload = _payload()
        payload["custos_auxiliares"][chave] = 74.4
        assert len(_aviso_do_cluster(_derivar(payload))) == 1, chave


def test_sem_kubernetes_nao_avisa() -> None:
    assert _aviso_do_cluster(_derivar()) == []


def test_rede_e_declarada_como_escolha_do_usuario() -> None:
    p = _premissa(_derivar(), "rede")
    assert p.categoria == "escolha_usuario"


# ── determinismo ────────────────────────────────────────────────────────


def test_duas_execucoes_produzem_o_mesmo_plano() -> None:
    """A razão de a derivação não passar por modelo. Um `.tf` que muda entre
    execuções não pode ser aplicado com confiança."""
    a, b = _derivar(), _derivar()
    assert [(r.logical_name, r.configuration) for r in a.recursos] == [
        (r.logical_name, r.configuration) for r in b.recursos
    ]
    assert [(p.campo, p.valor) for p in a.premissas] == [
        (p.campo, p.valor) for p in b.premissas
    ]


def test_payload_vazio_nao_explode() -> None:
    plano = derivar_plano({}, provedor_destino="gcp", regiao="us-central1")
    assert plano.recursos == []
    assert plano.avisos, "o pacote vazio tem que dizer por que está vazio"


# ── unicidade de nomes ──────────────────────────────────────────────────


def _pool(familia: str, regiao: str | None = None) -> dict:
    d = {
        "familia": familia,
        "equivalentes": [
            {
                "provedor": "GCP",
                "sku": "n2-standard-2",
                "instancias": 1,
                "correspondencia_tecnica": {"vcpus": 2, "ram_gb": 8},
            }
        ],
    }
    if regiao:
        d["regiao"] = regiao
    return d


def _nomes(pools: list[dict]) -> list[str]:
    plano = _derivar({"provedor_origem": "aws", "pools_computacao": pools})
    return [r.logical_name for r in plano.recursos]


def test_mesma_familia_em_regioes_diferentes_nao_colide() -> None:
    """O pool é chaveado por (família, região). Nomear só pela família fazia os
    dois virarem o mesmo identificador — e o Terraform recusa o arquivo inteiro
    com 'Duplicate resource', não só o bloco repetido."""
    nomes = _nomes([_pool("M6I", "us-east-1"), _pool("M6I", "sa-east-1")])
    assert len(nomes) == len(set(nomes))
    assert "us_east_1" in nomes[0]


def test_familias_que_diferem_so_no_casing_nao_colidem() -> None:
    """`M6I` e `m6i` são distintas na origem e viram o mesmo nome depois do
    saneamento para minúsculas."""
    nomes = _nomes([_pool("M6I"), _pool("m6i")])
    assert len(nomes) == len(set(nomes))


def test_muitos_pools_geram_nomes_todos_distintos() -> None:
    nomes = _nomes([_pool(f"F{i}") for i in range(30)])
    assert len(nomes) == len(set(nomes)) == 30


def test_nome_hostil_nao_vira_sintaxe() -> None:
    """Um nome de família com aspas e chaves não pode escapar para o HCL."""
    nomes = _nomes([_pool('x" { evil = "')])
    assert all(c.isalnum() or c == "_" for c in nomes[0]), nomes[0]


def test_disco_acima_do_maximo_do_provedor_e_limitado_com_aviso() -> None:
    """O balde é o TOTAL da conta, somando muitos volumes na origem. No bill AWS
    real isso dá 71.520 GB de SSD — acima do teto de 65.536 GB de um persistent
    disk no GCP.

    O `terraform validate` passa (não confere faixa de valor) e o `apply` falha
    na cara do cliente. Melhor sair no limite, com o aviso dizendo o que sobrou.
    """
    payload = {"provedor_origem": "aws", "custos_auxiliares": {"armazenamento_ssd": 5721.57}}
    plano = _derivar(payload)
    disco = next(r for r in plano.recursos if "size_gb" in r.configuration)
    assert disco.configuration["size_gb"] == 65536
    assert any("excede o máximo" in a for a in plano.avisos)


def test_pool_coberto_por_compromisso_carrega_a_ressalva() -> None:
    """O recurso está certo — a capacidade foi medida. O que não vale é comparar
    custo contra ele, porque a origem aparece com zero."""
    payload = _payload()
    payload["pools_computacao"][0]["custo_coberto_por_compromisso"] = True
    p = _premissa(_derivar(payload), "vm_m6i.machine_type")
    assert "compromisso" in p.base
    assert "Savings Plan" in p.base


def test_a_base_da_premissa_cabe_no_schema() -> None:
    """A base acumula o raciocínio inteiro. Já estourou o limite e derrubou a
    geração num pool coberto por compromisso — que é justamente o caso que mais
    precisa de explicação."""
    payload = _payload()
    pool = payload["pools_computacao"][0]
    pool["custo_coberto_por_compromisso"] = True
    pool["equivalentes"][0]["financeiro"] = {
        "confianca_preco": "low",
        "preco_origem": "seed_catalog",
        "preco_idade_dias": 120,
    }
    for premissa in _derivar(payload).premissas:
        assert len(premissa.base) <= _BASE_MAXIMA, premissa.campo
