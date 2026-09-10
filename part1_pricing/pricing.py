"""
Part 1 - Pricing Model: two-phase PropAMM quote.

  1. Stable phase: the part of a trade that moves the pool toward 50/50
     (in oracle value) executes at the flat oracle price P.
  2. Curve phase: the remainder executes on the concentrated CPMM
         (x + vx)(y + vy) = L^2,  vx = x * (alpha - 1),  vy = y * (alpha - 1).

Key observation: (x + vx) = alpha * x and (y + vy) = alpha * y, so the
curve-implied marginal price is cpPrice = y / x. Alpha changes depth
(slippage), not the starting price.

Units: X = base token (WBNB), Y = quote token (USDT). Every price
(price_P, cpPrice, bid, ask, effective_price) is in Y per X.

Modeling assumptions A1-A10 are documented in MODEL.md; inline comments
below reference them by id.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class InsufficientLiquidity(Exception):
    """Raised when a quote would require more real reserve than the pool holds."""


@dataclass(frozen=True)
class QuoteBreakdown:
    """Per-phase amounts plus the curve price before and the real Y/X ratio after.

    reserve_ratio_after is the final real reserve ratio (fees excluded); it is
    not the endpoint price of the curve used for this trade.
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
    # A10
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
    """Input that can fill at P before the pool is 50/50 in oracle value (A1, A2).

    Y->X: (rx*P - ry) / 2.   X->Y: (ry - rx*P) / (2P).   Zero if the direction
    would worsen the imbalance. Derivation in MODEL.md, "Stable capacity".
    """
    value_gap = reserve_x * price_P - reserve_y  # > 0 means X-heavy
    if swap_x_to_y:
        return max(0.0, -value_gap / (2 * price_P))
    return max(0.0, value_gap / 2)


def _curve_out(
    reserve_x: float, reserve_y: float, alpha: float, amount_in: float, swap_x_to_y: bool
) -> float:
    """CPMM output on effective reserves X = alpha*rx, Y = alpha*ry (A3).

    Y->X: X*r / (Y + r).   X->Y: Y*r / (X + r).   See MODEL.md, "Curve output".
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

    # A4: fee on the input token; only the net amount is priced.
    fee_charged = amount_in * (fee_bps / 10_000)
    net_in = amount_in - fee_charged

    cp_before = reserve_y / reserve_x

    # Phase 1: stable phase at flat price P.
    capacity = _stable_capacity(reserve_x, reserve_y, price_P, swap_x_to_y)
    stable_in = min(net_in, capacity)
    if swap_x_to_y:
        stable_out = stable_in * price_P
        rx, ry = reserve_x + stable_in, reserve_y - stable_out
    else:
        stable_out = stable_in / price_P
        rx, ry = reserve_x - stable_out, reserve_y + stable_in

    # Phase 2: curve phase built from the post-stable reserves (A3).
    curve_in = net_in - stable_in
    curve_out = _curve_out(rx, ry, alpha, curve_in, swap_x_to_y)
    if swap_x_to_y:
        rx, ry = rx + curve_in, ry - curve_out
    else:
        rx, ry = rx - curve_out, ry + curve_in

    amount_out = stable_out + curve_out

    # A6: virtual liquidity is not withdrawable; reject rather than clamp.
    real_reserve_out = reserve_y if swap_x_to_y else reserve_x
    if amount_out >= real_reserve_out:
        raise InsufficientLiquidity(
            f"amount_out={amount_out:.6f} would exhaust real reserve {real_reserve_out}"
        )

    # A5: all-in price in Y per X, computed from the gross amount_in.
    effective_price = amount_out / amount_in if swap_x_to_y else amount_in / amount_out

    breakdown = QuoteBreakdown(
        stable_in=stable_in,
        stable_out=stable_out,
        curve_in=curve_in,
        curve_out=curve_out,
        cp_price_before=cp_before,
        reserve_ratio_after=ry / rx,
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
    """Quote a swap. Returns (amount_out, effective_price, fee_charged).

    amount_out is in the output token, effective_price is the all-in price in
    Y per X, fee_charged is in the input token.
    """
    amount_out, effective_price, fee_charged, _ = get_quote_detailed(
        reserve_x, reserve_y, price_P, alpha, fee_bps, amount_in, swap_x_to_y
    )
    return amount_out, effective_price, fee_charged


def get_bid_ask(
    reserve_x: float, reserve_y: float, price_P: float, alpha: float
) -> tuple[float, float]:
    """Pre-fee marginal (bid, ask) in Y per X (A7).

    bid = min(P, cpPrice), ask = max(P, cpPrice) with cpPrice = ry / rx, so
    bid <= P <= ask: the pool never quotes better than the oracle. Alpha does
    not enter because it scales both reserves equally.
    """
    _validate(reserve_x, reserve_y, price_P, alpha)
    cp_price = reserve_y / reserve_x
    return min(price_P, cp_price), max(price_P, cp_price)


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #

OFFICIAL_CASES = {
    "A": dict(reserve_x=100, reserve_y=62_700, price_P=627, alpha=1.02, amount_in=500, swap_x_to_y=False),
    "B": dict(reserve_x=80, reserve_y=75_000, price_P=627, alpha=1.02, amount_in=500, swap_x_to_y=False),
    "C": dict(reserve_x=120, reserve_y=50_000, price_P=627, alpha=1.02, amount_in=500, swap_x_to_y=False),
    "D": dict(reserve_x=100, reserve_y=62_700, price_P=627, alpha=1.05, amount_in=500, swap_x_to_y=False),
}

# E: Case C reserves with a trade large enough to cross from stable into curve.
CROSSING_CASES = {
    "E": dict(reserve_x=120, reserve_y=50_000, price_P=627, alpha=1.02, amount_in=20_000, swap_x_to_y=False),
}

# F, G: reverse direction (trader sells WBNB). F is Y-heavy so selling X
# rebalances and fills at P; G is balanced so it goes straight to the curve.
REVERSE_CASES = {
    "F": dict(reserve_x=80, reserve_y=75_000, price_P=627, alpha=1.02, amount_in=1, swap_x_to_y=True),
    "G": dict(reserve_x=100, reserve_y=62_700, price_P=627, alpha=1.02, amount_in=1, swap_x_to_y=True),
}

ALL_CASES = {**OFFICIAL_CASES, **CROSSING_CASES, **REVERSE_CASES}


def _pool_state(rx: float, ry: float, P: float) -> str:
    gap = rx * P - ry
    if abs(gap) < 1e-9:
        return "balanced"
    return "X-heavy" if gap > 0 else "Y-heavy"


def _run_table(cases: dict, fee_bps: float) -> list[str]:
    lines = [
        f"#### fee_bps = {fee_bps:g}",
        "",
        "| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity "
        "| stable_in | curve_in | amount_out | eff. price | fee |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, c in cases.items():
        d = c["swap_x_to_y"]
        bid, ask = get_bid_ask(c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"])
        out, eff, fee, bd = get_quote_detailed(
            c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"],
            fee_bps, c["amount_in"], swap_x_to_y=d,
        )
        state = _pool_state(c["reserve_x"], c["reserve_y"], c["price_P"])
        capacity = _stable_capacity(c["reserve_x"], c["reserve_y"], c["price_P"], d)
        input_decimals = 4 if d else 2
        lines.append(
            f"| {name} | {state} | {c['alpha']:.2f} | {bd.cp_price_before:.2f} "
            f"| {bid:.2f} | {ask:.2f} | {capacity:,.{input_decimals}f} "
            f"| {bd.stable_in:,.{input_decimals}f} | {bd.curve_in:,.{input_decimals}f} "
            f"| {out:,.6f} | {eff:.4f} | {fee:.4f} |"
        )
    lines.append("")
    return lines


def _sanity_checks() -> list[str]:
    """Assertions that encode the expected qualitative behaviour."""
    P = 627
    notes = []

    # Case C: fully stable, effective price == P at zero fee.
    _, eff, _, bd = get_quote_detailed(120, 50_000, P, 1.02, 0, 500, False)
    assert abs(eff - P) < 1e-9, eff
    assert bd.curve_in == 0
    notes.append("C: entirely Stable at P = 627.")

    # Case A vs D: larger alpha => deeper curve => slightly more output.
    out_a, _, _ = get_quote(100, 62_700, P, 1.02, 0, 500, False)
    out_d, _, _ = get_quote(100, 62_700, P, 1.05, 0, 500, False)
    assert out_d > out_a
    notes.append("D returns more WBNB than A as alpha increases.")

    # Case E: stable capacity is exactly (rx*P - ry)/2 = 12,620.
    _, _, _, bd_e = get_quote_detailed(120, 50_000, P, 1.02, 0, 20_000, False)
    assert abs(bd_e.stable_in - 12_620) < 1e-9, bd_e.stable_in
    assert bd_e.curve_in > 0
    notes.append("E: 12,620 USDT Stable + 7,380 USDT Curve.")

    # Case F: X->Y on a Y-heavy pool is fully stable at P.
    out, eff, _, _ = get_quote_detailed(80, 75_000, P, 1.02, 0, 1.0, True)
    assert abs(eff - P) < 1e-9 and abs(out - P) < 1e-9
    notes.append("F: entirely Stable at P = 627.")

    # Effective price never beats the marginal bid/ask in any case.
    for c in ALL_CASES.values():
        bid, ask = get_bid_ask(c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"])
        _, eff, _, _ = get_quote_detailed(
            c["reserve_x"], c["reserve_y"], c["price_P"], c["alpha"],
            0, c["amount_in"], c["swap_x_to_y"],
        )
        assert eff <= bid + 1e-9 if c["swap_x_to_y"] else eff >= ask - 1e-9
    notes.append("A-G: effective prices respect marginal bid/ask bounds.")

    # No-arbitrage against the oracle: bid <= P <= ask, so a zero-fee round
    # trip always returns less than it started with.
    for x, y in ((100, 62_700), (80, 75_000), (120, 50_000)):
        bid, ask = get_bid_ask(x, y, P, 1.02)
        assert bid <= P <= ask
        ox, _, _ = get_quote(x, y, P, 1.02, 0, 500, False)
        back, _, _ = get_quote(x - ox, y + 500, P, 1.02, 0, ox, True)
        assert back < 500, back
    notes.append("A-C reserves: tested zero-fee round trips return less than 500 USDT.")

    # Rejection: a trade that would drain the real reserve is rejected.
    try:
        get_quote(100, 62_700, P, 1.02, 0, 10_000_000, False)
        raise AssertionError("expected InsufficientLiquidity")
    except InsufficientLiquidity:
        notes.append("Oversized input raises InsufficientLiquidity.")

    return notes


def main() -> str:
    lines = [
        "## Results", "",
        "P = 627 USDT/WBNB; all prices use USDT/WBNB. Capacity, phase inputs, and fee",
        "use the input token; amount_out uses the output token. Fee rate appears only in headings.",
        "", "### Official cases A-D", "", "USDT -> WBNB; gross input: 500 USDT per case.", "",
    ]
    lines += _run_table(OFFICIAL_CASES, 0)
    lines += _run_table(OFFICIAL_CASES, 5)
    lines += ["### Crossing case E", "", "Case C reserves; USDT -> WBNB; gross input: 20,000 USDT.", ""]
    lines += _run_table(CROSSING_CASES, 0)
    lines += [
        "### Reverse-direction cases F-G", "",
        "WBNB -> USDT; gross input: 1 WBNB. F uses Case B reserves; G uses Case A reserves.", "",
    ]
    lines += _run_table(REVERSE_CASES, 0)
    lines.append("### Sanity checks / observations")
    lines.append("")
    for note in _sanity_checks():
        lines.append(f"- PASS: {note}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    from argparse import ArgumentParser
    from pathlib import Path

    parser = ArgumentParser(description="Print pricing results or refresh them in MODEL.md.")
    parser.add_argument("--write-results", action="store_true", help="Refresh the generated results in MODEL.md")
    args = parser.parse_args()
    results = main()
    if args.write_results:
        model_path = Path(__file__).with_name("MODEL.md")
        document = model_path.read_text(encoding="utf-8")
        start = "<!-- BEGIN GENERATED RESULTS -->"
        end = "<!-- END GENERATED RESULTS -->"
        if document.count(start) != 1 or document.count(end) != 1 or document.index(start) >= document.index(end):
            raise ValueError("MODEL.md must contain exactly one ordered pair of result markers")
        before, remainder = document.split(start, 1)
        _, after = remainder.split(end, 1)
        model_path.write_text(before + start + "\n\n" + results.rstrip() + "\n\n" + end + after, encoding="utf-8")
        print(f"Updated {model_path}")
    else:
        print(results)
