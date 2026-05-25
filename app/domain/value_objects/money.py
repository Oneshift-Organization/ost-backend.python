from dataclasses import dataclass
from decimal import Decimal

ISO4217_LEN = 3


@dataclass(frozen=True, slots=True)
class Money:
    """Frozen monetary value. Internal representation is always Decimal.

    Construction:
        Money.of("123.45", "USD")  → recommended (no float at any point)
        Money(amount=Decimal("123.45"), currency="USD")  → also valid
    """

    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be Decimal")
        if not self.currency or len(self.currency) != ISO4217_LEN:
            raise ValueError("Money.currency must be ISO-4217 code (3 chars)")

    @classmethod
    def of(cls, value: str | int | Decimal, currency: str = "USD") -> "Money":
        """Build Money from any non-float value. Floats are rejected on purpose."""
        if isinstance(value, float):
            raise TypeError("Money rejects float input; pass str/int/Decimal")
        return cls(amount=Decimal(str(value)), currency=currency.upper())

    @classmethod
    def zero(cls, currency: str = "USD") -> "Money":
        return cls(amount=Decimal("0"), currency=currency.upper())

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int | Decimal) -> "Money":
        if isinstance(factor, float):
            raise TypeError("Money.__mul__ rejects float factor")
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def is_negative(self) -> bool:
        return self.amount < 0
