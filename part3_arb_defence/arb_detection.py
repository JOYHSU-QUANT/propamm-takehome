"""
Part 3 - Flashloan arb detection against the PropAMM.

is_suspicious_trade(tx_data) returns True when one transaction contains a
round trip through the PropAMM whose PropAMM leg filled at a price worse for
the pool than a reference by more than a threshold. A flashloan bracket
raises severity but is not required: the loss comes from the stale price,
not from the borrowed capital. Rationale and heuristics: DEFENCE.md, "Detect".

tx_data fields
--------------
  swaps          list of {protocol, token_in, token_out, amount_in, amount_out}, in call order
  transfers      list of {token, from, to, amount}, in log order (optional; used for flashloan
                 detection when interaction labels are missing)
  interactions   list of protocol labels in call order, e.g. ["ListaDAO", "PropAMM", "PancakeV2"]
  caller         address that initiated the swaps (msg.sender to the PropAMM)
  timestamp      block timestamp, unix seconds
  block_number   int
  base_token     symbol of the base asset (default "WBNB")
  quote_token    symbol of the quote asset (default "USDT")
  reference_price  optional fair price in quote per base at that block (e.g. Binance mid).
                   When absent, the other venue's fill price is used as the reference,
                   which is conservative: it cannot tell which venue was stale.

Prices are always quote per base (USDT per WBNB).
"""

from __future__ import annotations

from dataclasses import dataclass, field

PROPAMM = "PropAMM"
FLASHLOAN_PROTOCOLS = {"ListaDAO", "Venus", "Aave", "Radiant", "PancakeV3Flash", "Balancer"}

# A gap larger than fee + spread + normal slippage. 30 bps is far above the
# 5 bps spread in the assignment and far below the 6% seen in the attack.
DEFAULT_PRICE_GAP_BPS = 30.0
# The second leg is "funded by" the first if it spends at most the first leg's
# output (plus tolerance for rounding). Spending less is allowed: the trader
# may keep part of the proceeds.
CHAIN_TOLERANCE = 0.01


@dataclass(frozen=True)
class Leg:
    protocol: str
    token_in: str
    token_out: str
    amount_in: float
    amount_out: float
    index: int

    def price(self, base: str, quote: str) -> float | None:
        """Implied fill price in quote per base, or None if not the base/quote pair."""
        if self.token_in == quote and self.token_out == base:
            return self.amount_in / self.amount_out
        if self.token_in == base and self.token_out == quote:
            return self.amount_out / self.amount_in
        return None

    def sells_base(self, base: str) -> bool:
        """True if the venue handed out base (trader bought base)."""
        return self.token_out == base


@dataclass
class Analysis:
    suspicious: bool = False
    round_trip: bool = False
    flashloan: bool = False
    propamm_price: float | None = None
    other_price: float | None = None
    reference_price: float | None = None
    pool_disadvantage_bps: float | None = None
    # Gross: before flashloan fee, gas and any other execution cost.
    trader_gross_profit_quote: float | None = None
    pairs_checked: int = 0
    reasons: list[str] = field(default_factory=list)


def _legs(tx_data: dict) -> list[Leg]:
    return [
        Leg(s["protocol"], s["token_in"], s["token_out"],
            float(s["amount_in"]), float(s["amount_out"]), i)
        for i, s in enumerate(tx_data.get("swaps", []))
    ]


def _chained(first: Leg, second: Leg) -> bool:
    """second (later in call order) reverses first and is funded by its output."""
    if first.index >= second.index:
        return False
    if first.token_out != second.token_in or first.token_in != second.token_out:
        return False
    return second.amount_in <= first.amount_out * (1 + CHAIN_TOLERANCE)


def _has_flashloan(tx_data: dict) -> bool:
    inter = list(tx_data.get("interactions", []))
    # Borrow and repay bracket the trade: same lender first and last, with at
    # least one other call in between.
    if len(inter) >= 3 and inter[0] == inter[-1] and inter[0] in FLASHLOAN_PROTOCOLS:
        return True
    # Fallback on transfers: same token goes lender -> caller first and caller -> lender
    # last, with at least the borrowed amount returned.
    caller = tx_data.get("caller")
    transfers = tx_data.get("transfers", [])
    if not caller or len(transfers) < 2:
        return False
    first, last = transfers[0], transfers[-1]
    return (
        first.get("to") == caller and last.get("from") == caller
        and first.get("token") == last.get("token")
        and first.get("from") == last.get("to")
        and float(last.get("amount", 0)) >= float(first.get("amount", 0))
    )


def _gross_profit_quote(first: Leg, second: Leg, base: str, quote: str, ref: float) -> float:
    """Net token flow to the trader over both legs, valued in quote at ref."""
    net = {base: 0.0, quote: 0.0}
    for leg in (first, second):
        net[leg.token_out] += leg.amount_out
        net[leg.token_in] -= leg.amount_in
    return net[quote] + net[base] * ref


def analyse_trade(tx_data: dict, price_gap_bps: float = DEFAULT_PRICE_GAP_BPS) -> Analysis:
    """Full diagnostic. is_suspicious_trade() is a thin wrapper over this."""
    base = tx_data.get("base_token", "WBNB")
    quote = tx_data.get("quote_token", "USDT")
    a = Analysis(flashloan=_has_flashloan(tx_data))
    legs = [l for l in _legs(tx_data) if l.price(base, quote) is not None]
    propamm_legs = [l for l in legs if l.protocol == PROPAMM]
    if not propamm_legs:
        a.reasons.append("no PropAMM swap on the base/quote pair")
        return a

    ref_given = tx_data.get("reference_price")

    # Evaluate every chained (PropAMM, other-venue) pair in call order and keep
    # the one that is worst for the pool. A benign round trip earlier in the
    # same transaction must not hide a later attack.
    worst = None
    for pa in propamm_legs:
        for other in legs:
            if other.protocol == PROPAMM:
                continue
            if _chained(pa, other):
                first, second = pa, other
            elif _chained(other, pa):
                first, second = other, pa
            else:
                continue
            a.pairs_checked += 1
            ref = float(ref_given) if ref_given else other.price(base, quote)
            pa_price = pa.price(base, quote)
            # Pool loses when it sells base below the reference or buys base above it.
            if pa.sells_base(base):
                disadvantage_bps = (ref - pa_price) / ref * 1e4
            else:
                disadvantage_bps = (pa_price - ref) / ref * 1e4
            candidate = (disadvantage_bps, pa, other, first, second, ref)
            if worst is None or disadvantage_bps > worst[0]:
                worst = candidate

    if worst is None:
        a.reasons.append("no reverse leg on another venue funded by the PropAMM swap (or vice versa)")
        return a

    disadvantage_bps, pa, other, first, second, ref = worst
    a.round_trip = True
    a.propamm_price = pa.price(base, quote)
    a.other_price = other.price(base, quote)
    a.reference_price = ref
    a.pool_disadvantage_bps = disadvantage_bps
    a.trader_gross_profit_quote = _gross_profit_quote(first, second, base, quote, ref)

    if disadvantage_bps >= price_gap_bps:
        a.suspicious = True
        a.reasons.append(
            f"PropAMM leg filled {disadvantage_bps:.1f} bps against the pool "
            f"(PropAMM {a.propamm_price:.2f} vs reference {ref:.2f}, threshold {price_gap_bps:.0f} bps)"
        )
        if a.flashloan:
            a.reasons.append("flashloan bracket present: capital-free, atomic execution")
    else:
        a.reasons.append(
            f"round trip present but PropAMM leg within {price_gap_bps:.0f} bps of reference "
            f"({disadvantage_bps:.1f} bps)"
        )
    if a.pairs_checked > 1:
        a.reasons.append(f"{a.pairs_checked} chained pairs checked; worst reported")
    return a


def is_suspicious_trade(tx_data: dict, price_gap_bps: float = DEFAULT_PRICE_GAP_BPS) -> bool:
    """True if the trade pattern matches a (flashloan) arb against the PropAMM."""
    return analyse_trade(tx_data, price_gap_bps).suspicious


# Observed transaction from the assignment (BSC, stale oracle ~6% below market).
OBSERVED_ATTACK = {
    "swaps": [
        {"protocol": "PropAMM", "token_in": "USDT", "token_out": "WBNB", "amount_in": 10.0, "amount_out": 0.01702},
        {"protocol": "PancakeV2", "token_in": "WBNB", "token_out": "USDT", "amount_in": 0.01702, "amount_out": 10.627},
    ],
    "transfers": [
        {"token": "USDT", "from": "ListaDAO", "to": "0xattacker", "amount": 10.0},
        {"token": "USDT", "from": "0xattacker", "to": "PropAMM", "amount": 10.0},
        {"token": "WBNB", "from": "PropAMM", "to": "0xattacker", "amount": 0.01702},
        {"token": "WBNB", "from": "0xattacker", "to": "PancakeV2", "amount": 0.01702},
        {"token": "USDT", "from": "PancakeV2", "to": "0xattacker", "amount": 10.627},
        {"token": "USDT", "from": "0xattacker", "to": "ListaDAO", "amount": 10.0},
    ],
    "interactions": ["ListaDAO", "PropAMM", "PancakeV2", "ListaDAO"],
    "caller": "0xattacker",
    "timestamp": 1_700_000_000,
    "block_number": 0,
    "reference_price": 624.0,
}


if __name__ == "__main__":
    result = analyse_trade(OBSERVED_ATTACK)
    print(f"suspicious           : {result.suspicious}")
    print(f"round trip           : {result.round_trip}")
    print(f"flashloan            : {result.flashloan}")
    print(f"PropAMM fill         : {result.propamm_price:.2f} USDT/WBNB")
    print(f"other venue fill     : {result.other_price:.2f} USDT/WBNB")
    print(f"reference            : {result.reference_price:.2f} USDT/WBNB")
    print(f"pool disadvantage    : {result.pool_disadvantage_bps:.1f} bps")
    print(f"trader gross profit  : {result.trader_gross_profit_quote:.3f} USDT (before loan fee and gas)")
    for r in result.reasons:
        print(f"  - {r}")
