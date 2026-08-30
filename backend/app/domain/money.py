"""Money rounding helpers. Rulebook section 1."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

ZERO = Decimal("0")
PENCE = Decimal("0.01")


def floor_money(x: Decimal) -> Decimal:
    """Round down to pence.

    Three deliberate choices:

    * **Pence, not pounds.** ``floor(600/28)`` is 21.42, not 21.00 -- flooring to
      whole pounds strands £12 of a £600 budget as permanently unspendable.
    * **ROUND_FLOOR, not ROUND_DOWN.** They agree wherever the value is positive
      and diverge exactly where deficits are reported: −12.501 floors to −12.51,
      but rounds *down* (toward zero) to −12.50.
    * **Never ``//`` on Decimal.** ``Decimal(-7) // Decimal(2)`` is −3 while
      ``-7 // 2`` is −4: Decimal floor-division truncates toward zero. A developer
      who checks the operator with ints in a REPL sees floor behaviour and ships a
      truncating money path. (Integer ``//`` is correct and required for the
      fortnightly period index -- the trap is Decimal-only.)
    """
    return x.quantize(PENCE, rounding=ROUND_FLOOR)
