import pytest

from app.domain.services.guarda_de_coleta import coleta_parcial


@pytest.mark.parametrize(
    ("vigentes", "trazidos", "parcial"),
    [
        # Conjunto grande que volta com menos da metade: coleta cortada
        (300, 100, True),
        (10, 4, True),
        # Encolher um pouco é a vida: SKU aposentado sai da cotação
        (300, 295, False),
        (10, 5, False),
        # Conjunto pequeno pode encolher de verdade
        (3, 1, False),
        (9, 0, False),
        # Primeira coleta e coleta que cresceu
        (0, 50, False),
        (100, 150, False),
    ],
)
def test_coleta_parcial(vigentes: int, trazidos: int, parcial: bool) -> None:
    assert coleta_parcial(vigentes, trazidos) is parcial
