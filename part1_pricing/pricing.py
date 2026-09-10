"""
Part 1 - Pricing Model

Two-phase PropAMM quote:

  1. Stable phase: any part of the trade that moves the pool toward 50/50
     (measured in oracle value) executes at the flat oracle price P.
  2. Curve phase: the remainder executes on a concentrated CPMM
         (x + virtual_x)(y + virtual_y) = L^2
     with virtual_x = reserve_x * (alpha - 1), virtual_y = reserve_y * (alpha - 1).

Key observation
---------------
Because the virtual reserves scale both sides by the same factor,
    (x + vx) = alpha * x,   (y + vy) = alpha * y
so the curve-implied marginal price is simply
    cpPrice = reserve_y / reserve_x
Alpha therefore does not change the starting price of the curve, only its depth
(how fast the price moves as the trade consumes liquidity).

Units
-----
- X = base token (WBNB), Y = quote token (USDT).
- price_P, cpPrice, bid, ask and effective_price are all expressed in Y per X.

Assumptions (also listed in MODEL.md)
-------------------------------------
A1. "50/50" is measured in oracle value: reserve_x * P vs reserve_y.
A2. A trade only enters the stable phase if it moves the pool toward 50/50.
    It executes at P until the pool is exactly balanced; the remainder goes
    to the curve.
A3. The curve phase uses the reserves *after* the stable phase to build the
    virtual reserves. If a trade crosses from stable to curve, the pool is
    balanced at the boundary, so the curve starts exactly at P.
A4. Fee is charged on the input token: fee_charged = amount_in * fee_bps / 1e4.
    The net amount (amount_in - fee) is what gets priced.
A5. effective_price is the all-in price the trader pays, i.e. it is computed
    from the gross amount_in (fee included).
A6. Virtual liquidity changes pricing depth but cannot create transferable
    tokens. If amount_out would reach or exceed the real reserve, the quote
    is rejected (InsufficientLiquidity) rather than clamped, because clamping
    would silently change the execution price.
A7. get_bid_ask returns pre-fee marginal prices (its signature has no fee_bps).
A8. The test table does not specify fee_bps. The canonical results are
    reported with fee_bps = 0 to isolate the pricing model; a 5 bps run is
    included for illustration only.
A9. Fees are accounted for separately and excluded from the simulated pricing
    reserves. Each call rebuilds virtual reserves; no invariant is persisted
    between calls, so splitting a trade can change its total output. Under
    the same fee, fixed P and alpha, same direction, reserves updated by the
    net (post-fee) input after each fill, and both executions fillable,
    splitting never increases total output (ignoring floating-point
    rounding). Total output is strictly lower only when alpha > 1 and at
    least two sub-trades each carry positive curve input; otherwise it is
    identical. In particular, a stable -> curve crossing trade split exactly
    at the boundary, or anywhere before it, yields the same output as the
    single trade, because the curve portion is still executed in one piece.
    Example: Case A split into 2 x 250 USDT yields ~6.14e-5 WBNB less than
    one 500 USDT trade.
A10. Inputs must be finite, reserves/P/amount_in positive, alpha >= 1, and
     0 <= fee_bps < 10000. Calculations use floats in human token units;
     on-chain integer rounding is outside the scope of this model.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class InsufficientLiquidity(Exception):
    """Raised when a quote would require more real reserve than the pool holds."""


@dataclass(frozen=True)
class QuoteBreakdown:
    """Phase amounts and reserve ratios in Y per X.

    cp_price_before is the starting curve price. reserve_ratio_after is the
    final real Y/X ratio, excluding fees; it is not the endpoint price of the
    curve with this trade's fixed virtual reserves.
    """

    stable_in: float
    stable_out: float
    curve_in: float
    curve_out: float
    cp_price_before: float
    reserve_ratio_after: float


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _validate(reserve_x: float, reserve_y: float, price_P: float, alpha: float) -> None:
    for name, value in (
        ("reserve_x", reserve_x), ("reserve_y", reserve_y),
        ("price_P", price_P), ("alpha", alpha),
    ):
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")
    if reserve_x <= 0 or reserve_y <= 0:
        raise ValueError("reserves must be positive")
    if price_P <= 0:
        raise ValueError("price_P must be positive")
    if alpha < 1:
        raise ValueError("alpha must be >= 1")


def _stable_capacity(
    reserve_x: float, reserve_y: float, price_P: float, swap_x_to_y: bool
) -> float:
    """
    Maximum input amount that can execute at the flat oracle price P before the
    pool reaches 50/50 in oracle value. Returns 0 if the trade direction would
    worsen the imbalance.

    Y -> X (trader sells dy of Y, receives dy/P of X):
        balance when (rx - dy/P) * P == ry + dy   =>  dy* = (rx*P - ry) / 2
    X -> Y (trader sells dx of X, receives dx*P of Y):
        balance when (rx + dx) * P == ry - dx*P   =>  dx* = (ry - rx*P) / (2P)
    """
    value_gap = reserve_x * price_P - reserve_y  # > 0 means X-heavy
    if swap_x_to_y:
        # Trader adds X. Only helps if the pool is Y-heavy (value_gap < 0).
        return max(0.0, -value_gap / (2 * price_P))
    # Trader adds Y. Only helps if the pool is X-heavy (value_gap > 0).
    return max(0.0, value_gap / 2)


def _curve_out(
    reserve_x: float, reserve_y: float, alpha: float, amount_in: float, swap_x_to_y: bool
) -> float:
    """
    Output of a trade of size amount_in on the concentrated curve
        (x + vx)(y + vy) = L^2,  vx = rx*(alpha-1), vy = ry*(alpha-1)
    which is a CPMM on the effective reserves X = alpha*rx, Y = alpha*ry.
    """
    if amount_in <= 0:
        return 0.0
    X = alpha * reserve_x
    Y = alpha * reserve_y
    if swap_x_to_y:
        return Y * (amount_in / (X + amount_in))
    return X * (amount_in / (Y + amount_in))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def get_quote_detailed(
    reserve_x: float,
    reserve_y: float,
    price_P: float,
    alpha: float,
    fee_bps: float,
    amount_in: float,
    swap_x_to_y: bool,
) -> tuple[float, float, float, QuoteBreakdown]:
    """Same as get_quote but also returns a QuoteBreakdown for diagnostics."""
    _validate(reserve_x, reserve_y, price_P, alpha)
    if not isfinite(amount_in) or amount_in <= 0:
        raise ValueError("amount_in must be finite and positive")
    if not isfinite(fee_bps) or not 0 <= fee_bps < 10_000:
        raise ValueError("fee_bps must be finite and in [0, 10000)")
    if not isinstance(swap_x_to_y, bool):
        raise ValueError("swap_x_to_y must be a bool")

    # A4: fee on the input token.
    fee_charged = amount_in * (fee_bps / 10_000)
    net_in = amount_in - fee_charged

    cp_before = reserve_y / reserve_x

    # Phase 1: stable phase at flat price P (A1, A2).
    capacity = _stable_capacity(reserve_x, reserve_y, price_P, swap_x_to_y)
    stable_in = min(net_in, capacity)
    if swap_x_to_y:
        stable_out = stable_in * price_P
        rx, ry = reserve_x + stable_in, reserve_y - stable_out
    else:
        stable_out = stable_in / price_P
        rx, ry = reserve_x - stable_out, reserve_y + stable_in

    # Phase 2: curve phase on post-stable reserves (A3).
    curve_in = net_in - stable_in
    curve_out = _curve_out(rx, ry, alpha, curve_in, swap_x_to_y)
    if swap_x_to_y:
        rx, ry = rx + curve_in, ry - curve_out
    else:
        rx, ry = rx - curve_out, ry + curve_in

    amount_out = stable_out + curve_out
    if not isfinite(amount_out) or amount_out <= 0:
        raise ValueError("amount_out must be finite and positive; check numeric scale")

    # A6: virtual liquidity is not withdrawable.
    real_reserve_out = reserve_y if swap_x_to_y else reserve_x
    if amount_out >= real_reserve_out:
        raise InsufficientLiquidity(
            f"amount_out={amount_out:.6f} would exhaust real reserve {real_reserve_out}"
        )

    # A5: all-in price in Y per X, computed from gross amount_in.
    if swap_x_to_y:
        effective_price = amount_out / amount_in
    else:
        effective_price = amount_in / amount_out
    reserve_ratio_after = ry / rx
    if not all(isfinite(value) and value > 0 for value in (
        effective_price, cp_before, reserve_ratio_after,
    )):
        raise ValueError("computed prices must be finite and positive; check numeric scale")

    breakdown = QuoteBreakdown(
        stable_in=stable_in,
        stable_out=stable_out,
        curve_in=curve_in,
        curve_out=curve_out,
        cp_price_before=cp_before,
        reserve_ratio_after=reserve_ratio_after,
    )
    return amount_out, effective_price, fee_charged, breakdown


def get_quote(
    reserve_x: float,
    reserve_y: float,
    price_P: float,
    alpha: float,
    fee_bps: float,
    amount_in: float,
    swap_x_to_y: bool,
) -> tuple[float, float, float]:
    """
    Quote a swap against the two-phase PropAMM.

    Returns (amount_out, effective_price, fee_charged) where
      - amount_out is in the output token,
      - effective_price is the all-in price in Y per X,
      - fee_charged is in the input token.
    """
    amount_out, effective_price, fee_charged, _ = get_quote_detailed(
        reserve_x, reserve_y, price_P, alpha, fee_bps, amount_in, swap_x_to_y
    )
    return amount_out, effective_price, fee_charged


def get_bid_ask(
    reserve_x: float, reserve_y: float, price_P: float, alpha: float
) -> tuple[float, float]:
    """
    Pre-fee marginal bid/ask in Y per X.

    A trade that rebalances the pool executes at P; a trade that worsens the
    imbalance executes on the curve whose marginal price is cpPrice = ry/rx.
    Hence
        bid = min(P, cpPrice)   (price the pool pays when it buys X)
        ask = max(P, cpPrice)   (price the pool charges when it sells X)
    and the spread is |P - cpPrice|. When balanced, bid = ask = P.
    Alpha does not affect the marginal price (see module docstring).
    """
    _validate(reserve_x, reserve_y, price_P, alpha)
    cp_price = reserve_y / reserve_x
    if not isfinite(cp_price) or cp_price <= 0:
        raise ValueError("computed curve price must be finite and positive; check numeric scale")
    return min(price_P, cp_price), max(price_P, cp_price)


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #

OFFICIAL_CASES = {
    "A": dict(reserve_x=100, reserve_y=62_700, price_P=627, alpha=1.02, amount_in=500),
    "B": dict(reserve_x=80, reserve_y=75_000, price_P=627, alpha=1.02, amount_in=500),
    "C": dict(reserve_x=120, reserve_y=50_000, price_P=627, alpha=1.02, amount_in=500),
    "D": dict(reserve_x=100, reserve_y=62_700, price_P=627, alpha=1.05, amount_in=500),
}

# Extra case: Case C reserves with a trade large enough to cross from the
# stable phase into the curve phase. None of the official cases exercise this.
EXTRA_CASES = {
    "E": dict(reserve_x=120, reserve_y=50_000, price_P=627, alpha=1.02, amount_in=20_000),
}


def _pool_state(rx: float, ry: float, P: float) -> str:
    gap = rx * P - ry
    if abs(gap) < 1e-9:
        return "balanced"
    return "X-heavy" if gap > 0 else "Y-heavy"


def _run_table(cases: dict, fee_bps: float, title: str) -> list[str]:
    lines = [
        f"### {title} (fee_bps = {fee_bps}, all Y -> X)",
        "",
        "| Case | Pool | cpPrice | bid | ask | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, c in cases.items():
        bid, ask = get_bid_ask(c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"])
        out, eff, fee, bd = get_quote_detailed(
            c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"],
            fee_bps, c["amount_in"], swap_x_to_y=False,
        )
        state = _pool_state(c["reserve_x"], c["reserve_y"], c["price_P"])
        lines.append(
            f"| {name} | {state} | {bd.cp_price_before:.2f} | {bid:.2f} | {ask:.2f} "
            f"| {bd.stable_in:,.2f} | {bd.curve_in:,.2f} | {out:.6f} | {eff:.4f} | {fee:.4f} |"
        )
    lines.append("")
    return lines


def _sanity_checks() -> list[str]:
    """Assertions that encode the expected qualitative behaviour."""
    P = 627
    notes = []

    # Case C: fully stable, effective price == P at zero fee.
    out, eff, _, bd = get_quote_detailed(120, 50_000, P, 1.02, 0, 500, False)
    assert abs(eff - P) < 1e-9, eff
    assert bd.curve_in == 0
    notes.append("Case C executes entirely at P (eff. price == 627, curve_in == 0)")

    # Case A vs D: larger alpha => deeper curve => slightly more output.
    out_a, _, _, _ = get_quote_detailed(100, 62_700, P, 1.02, 0, 500, False)
    out_d, _, _, _ = get_quote_detailed(100, 62_700, P, 1.05, 0, 500, False)
    assert out_d > out_a
    notes.append("Case D (alpha 1.05) returns more WBNB than Case A (alpha 1.02)")

    # Case E: stable capacity is exactly (rx*P - ry)/2 = 12,620.
    _, _, _, bd_e = get_quote_detailed(120, 50_000, P, 1.02, 0, 20_000, False)
    assert abs(bd_e.stable_in - 12_620) < 1e-9, bd_e.stable_in
    assert bd_e.curve_in > 0
    notes.append("Case E splits: 12,620 USDT stable, 7,380 USDT curve")

    # Effective price is never better than the marginal ask for Y -> X.
    for c in {**OFFICIAL_CASES, **EXTRA_CASES}.values():
        _, ask = get_bid_ask(c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"])
        _, eff, _, _ = get_quote_detailed(
            c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"], 0, c["amount_in"], False
        )
        assert eff >= ask - 1e-9
    notes.append("Effective price >= marginal ask for every Y -> X case")

    # Symmetry: X -> Y on a Y-heavy pool is fully stable at P.
    out, eff, _, bd = get_quote_detailed(80, 75_000, P, 1.02, 0, 1.0, True)
    assert abs(eff - P) < 1e-9 and abs(out - P) < 1e-9
    notes.append("X -> Y on Y-heavy pool (Case B reserves) executes at P")

    # Rejection: a trade that would drain the real reserve is rejected.
    try:
        get_quote_detailed(100, 62_700, P, 1.02, 0, 10_000_000, False)
        raise AssertionError("expected InsufficientLiquidity")
    except InsufficientLiquidity:
        notes.append("Oversized trade raises InsufficientLiquidity instead of clamping")

    return notes


def main() -> str:
    lines = ["# Part 1 - Test Results", ""]
    lines += _run_table(OFFICIAL_CASES, 0, "Official cases")
    lines += _run_table(OFFICIAL_CASES, 5, "Official cases with an illustrative fee")
    lines += _run_table(EXTRA_CASES, 0, "Extra case E: stable -> curve crossing")
    lines.append("### Sanity checks")
    lines.append("")
    for note in _sanity_checks():
        lines.append(f"- PASS: {note}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(main())
