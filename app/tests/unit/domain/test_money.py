from decimal import Decimal

import pytest

from app.domain.value_objects.money import Money


def test_of_builds_money_from_string():
    m = Money.of("12.50", "USD")
    assert m.amount == Decimal("12.50")
    assert m.currency == "USD"


def test_of_rejects_float_input():
    with pytest.raises(TypeError):
        Money.of(12.5, "USD")  # type: ignore[arg-type]


def test_zero_factory():
    assert Money.zero("BRL").amount == Decimal("0")


def test_money_add_same_currency():
    a = Money.of("10", "USD")
    b = Money.of("5", "USD")
    assert (a + b).amount == Decimal("15")


def test_money_add_currency_mismatch_raises():
    with pytest.raises(ValueError):
        Money.of("10", "USD") + Money.of("5", "BRL")


def test_money_is_frozen():
    m = Money.of("1", "USD")
    with pytest.raises(AttributeError):
        m.amount = Decimal("2")  # type: ignore[misc]


def test_money_rejects_2char_currency():
    with pytest.raises(ValueError):
        Money(amount=Decimal("1"), currency="US")


def test_money_mul_rejects_float():
    m = Money.of("1", "USD")
    with pytest.raises(TypeError):
        m * 1.5  # type: ignore[operator]
