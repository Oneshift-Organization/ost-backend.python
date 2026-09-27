"""Grupos de segurança espelhados: tradução das regras e limite por interface (RI15.4)."""

from __future__ import annotations

from app.domain.services.terraform_plan import PlanoDerivado, RecursoDerivado
from app.domain.services.terraform_topologia.grupos import espelhar_grupos

_REDE = "10.50.0.0/16"


def _regra(origem: tuple[str, str | None], porta: int | None = 8080, **extra) -> dict:
    return {
        "acao": extra.pop("acao", "permitir"),
        "protocolo": extra.pop("protocolo", "tcp"),
        "porta_inicio": porta,
        "porta_fim": porta,
        "origem": {"tipo": origem[0], "valor": origem[1]},
        "destino": extra.pop("destino", None),
        "prioridade": None,
    }


def _plano(*grupos_por_vm: list[str]) -> PlanoDerivado:
    subrede = RecursoDerivado(
        "subnet_app_a",
        "subnet",
        {
            "cidr": "10.50.16.0/20",
            "mirror_of": {"chave": "s-app-a", "nome": "app-a", "cidr": "10.1.16.0/20"},
        },
    )
    vms = [
        RecursoDerivado(
            f"vm_{i}",
            "compute",
            {"instances": 1, "subnet": "subnet_app_a", "placement_groups": grupos},
        )
        for i, grupos in enumerate(grupos_por_vm)
    ]
    return PlanoDerivado(recursos=[subrede, *vms])


def _detalhe(grupos: dict[str, list[dict]], **rede) -> dict:
    return {
        "rede": {
            "grupos": [
                {"chave": g, "nome": g, "regras": regras, "restringe_saida": False}
                for g, regras in grupos.items()
            ],
            "grupos_referenciados": rede.get("referenciados", {}),
            "redes_origem": rede.get("redes", ["10.1.0.0/16"]),
            "subredes_origem": rede.get(
                "subredes", ["10.1.16.0/20", "10.1.32.0/20", "10.9.0.0/24"]
            ),
        }
    }


def _aplicar(plano: PlanoDerivado, detalhe: dict, destino: str = "aws", origem: str = "aws"):
    espelhar_grupos(plano, detalhe, destino=destino, rede=_REDE, provedor_origem=origem)
    return {
        r.logical_name: r.configuration
        for r in plano.recursos
        if r.resource_type == "security_group"
    }


def _fontes(grupo: dict) -> list[tuple]:
    return [
        (r["from_port"], tuple(r["source_cidrs"]), tuple(r["source_groups"]))
        for r in grupo["rules"]
    ]


def test_cidr_de_origem_traduzido_pela_forma() -> None:
    plano = _plano(["app"])
    grupos = _aplicar(
        plano,
        _detalhe(
            {
                "app": [
                    _regra(("cidr", "10.1.16.0/20"), 1),  # subrede espelhada inteira
                    _regra(
                        ("cidr", "10.1.17.0/24"), 2
                    ),  # pedaço dela: a subrede de destino inteira
                    _regra(("cidr", "10.1.0.0/16"), 3),  # a rede inteira: a rede de destino
                    _regra(("cidr", "10.9.0.0/24"), 4),  # subrede que não migrou: fica
                    _regra(("cidr", "203.0.113.0/24"), 5),  # externa: fica
                ]
            }
        ),
    )
    assert _fontes(grupos["sg_app"]) == [
        (1, ("10.50.16.0/20",), ()),
        (2, ("10.50.16.0/20",), ()),
        (3, (_REDE,), ()),
        (4, ("10.9.0.0/24",), ()),
        (5, ("203.0.113.0/24",), ()),
    ]
    [premissa] = [p for p in plano.premissas if p.campo == "grupos_de_seguranca"]
    assert "1 regra(s) citavam um pedaço de subrede" in premissa.base


def test_grupo_de_origem_espelhado_referenciado_ou_perdido() -> None:
    plano = _plano(["app", "banco"])
    grupos = _aplicar(
        plano,
        _detalhe(
            {
                "banco": [
                    _regra(("grupo", "app"), 5432),  # espelhado: referência
                    _regra(("grupo", "bastion"), 22),  # não migrou: CIDR dos membros
                    _regra(("grupo", "sumido"), 5432),  # sem membro conhecido: sai
                ],
                "app": [],
            },
            referenciados={"bastion": {"nome": "bastion", "cidrs": ["10.1.0.0/24"]}},
        ),
    )
    assert _fontes(grupos["sg_banco"]) == [(22, ("10.1.0.0/24",), ()), (5432, (), ("sg_app",))]
    assert any("grupo sem membro conhecido" in a for a in plano.avisos)


def test_regra_de_risco_alto_nao_e_replicada() -> None:
    plano = _plano(["app"])
    grupos = _aplicar(
        plano,
        _detalhe(
            {
                "app": [
                    _regra(("qualquer", None), 22),  # administração
                    _regra(("qualquer", None), 5432),  # banco
                    _regra(("qualquer", None), None, protocolo="todos"),  # tudo
                    _regra(("qualquer", None), 443),  # web aberta: segue
                ]
            }
        ),
    )
    assert _fontes(grupos["sg_app"]) == [(443, ("0.0.0.0/0",), ())]
    assert any("3 regra(s) de entrada não foram replicadas: risco alto" in a for a in plano.avisos)


def test_negar_protocolo_desconhecido_e_servicos() -> None:
    plano = _plano(["app"])
    grupos = _aplicar(
        plano,
        _detalhe(
            {
                "app": [
                    _regra(("cidr", "10.1.16.0/20"), 80, acao="negar"),
                    _regra(("cidr", "10.1.16.0/20"), None, protocolo="50"),
                    _regra(("servico", "AzureLoadBalancer"), 80),
                    _regra(("servico", "VirtualNetwork"), 80),
                    _regra(("cidr", "35.235.240.0/20"), 22),  # IAP do Google
                ]
            }
        ),
        origem="gcp",
    )
    assert _fontes(grupos["sg_app"]) == [(80, (_REDE,), ())]
    textos = " ".join(plano.avisos)
    for motivo in ("regra de negar", "protocolo fora", "rótulo de serviço", "IAP do Google"):
        assert motivo in textos, motivo


def test_asg_de_destino_estreita_o_alvo() -> None:
    """Regra do NSG com ASG de destino vale só para o grupo do ASG, não para o NSG."""
    plano = _plano(["nsg", "asg_web"])
    grupos = _aplicar(
        plano,
        _detalhe(
            {
                "nsg": [
                    _regra(
                        ("cidr", "10.1.16.0/20"),
                        8080,
                        destino={"tipo": "grupo", "valor": "asg_web"},
                    )
                ],
                "asg_web": [],
            }
        ),
        destino="azure",
    )
    assert grupos["sg_nsg"]["rules"] == []
    assert _fontes(grupos["sg_asg_web"]) == [(8080, ("10.50.16.0/20",), ())]


def test_limite_por_interface_gera_grupo_combinado() -> None:
    """Seis grupos numa VM passam dos 5 da AWS: ela recebe um combinado, e quem aceitava
    um dos seis passa a aceitar também o combinado."""
    seis = [f"g{i}" for i in range(6)]
    plano = _plano(seis, ["banco"])
    grupos = _aplicar(
        plano,
        _detalhe(
            {
                **{g: [_regra(("cidr", "10.1.16.0/20"), 8000 + i)] for i, g in enumerate(seis)},
                "banco": [_regra(("grupo", "g0"), 5432)],
            }
        ),
    )
    vm = next(r for r in plano.recursos if r.logical_name == "vm_0")
    [combinado] = vm.configuration["security_groups"]
    assert grupos[combinado]["combined"] is True
    assert sorted(r["from_port"] for r in grupos[combinado]["rules"]) == [
        8000 + i for i in range(6)
    ]
    assert _fontes(grupos["sg_banco"]) == [(5432, (), tuple(sorted(("sg_g0", combinado))))]
    # Na Azure (20 por NIC) não precisa.
    plano = _plano(seis)
    _aplicar(plano, _detalhe({g: [] for g in seis}), destino="azure")
    assert (
        len(
            next(r for r in plano.recursos if r.logical_name == "vm_0").configuration[
                "security_groups"
            ]
        )
        == 6
    )


def test_so_espelha_grupo_com_recurso_gerado() -> None:
    plano = _plano(["app"])
    grupos = _aplicar(plano, _detalhe({"app": [], "orfao": [_regra(("grupo", "app"), 80)]}))
    assert set(grupos) == {"sg_app"}


def test_exemplo_repetido_aparece_uma_vez() -> None:
    plano = _plano(["app"])
    _aplicar(
        plano,
        _detalhe({"app": [_regra(("cidr", "35.235.240.0/20"), 22) for _ in range(4)]}),
        origem="gcp",
    )
    [aviso] = [a for a in plano.avisos if "IAP" in a]
    assert aviso.startswith("4 regra(s)")
    assert aviso.count("IAP do Google") == 1

