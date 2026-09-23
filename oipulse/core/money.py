"""Monetary and quantity primitives.

`Decimal` throughout, never `float`. Option premia and strike arithmetic accumulate
rounding error quickly under binary floating point, and exposure figures multiply by lot
size — an error introduced here is scaled, not absorbed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

__all__ = ["Price", "Quantity", "to_decimal"]

Numeric = int | str | Decimal


def to_decimal(value: Numeric) -> Decimal:
    """Coerce to Decimal, rejecting float input.

    `float` is rejected rather than converted: accepting it would silently admit the
    binary-rounding error this module exists to avoid, and the call site is the only
    place that knows whether the value was ever exact.
    """
    if isinstance(value, float):
        raise TypeError("float rejected; pass int, str or Decimal to preserve exactness")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"cannot interpret {value!r} as a decimal") from exc


Price = Decimal
Quantity = Decimal
