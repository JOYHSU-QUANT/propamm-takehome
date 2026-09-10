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
# Two legs are "chained" if the second consumes the first's output within this tolerance.
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
    trader_profit_quote: float | None = None
    reasons: list[str] = field(default_factory=list)


def _legs(tx_data: dict) -> list[Leg]:
    return [
        Leg(s["protocol"], s["token_in"], s["token_out"],
            float(s["amount_in"]), float(s["amount_out"]), i)
        for i, s in enumerate(tx_data.get("swaps", []))
    ]


def _chained(a: Leg, b: Leg) -> bool:
    """b is the reverse leg of a and consumes (approximately) a's output."""
    if a.token_out != b.token_in or a.token_in != b.token_out:
        return False
    return abs(b.amount_in - a.amount_out) <= CHAIN_TOLERANCE * a.amount_out


def _has_flashloan(tx_data: dict) -> bool:
    inter = list(tx_data.get("interactions", []))
    if inter and inter[0] in FLASHLOAN_PROTOCOLS and inter[-1] in FLASHLOAN_PROTOCOLS:
        return True
    # Fallback on transfers: same token goes lender -> caller first and caller -> lender
    # last, with at least the borrowed amount returned.
    caller = tx_data.get("caller")
    transfers = tx_data.get("transfers", [])
    if not caller or not transfers:
        return False
    first, last = transfers[0], transfers[-1]
    return (
        first.get("to") == caller and last.get("from") == caller
        and first.get("token") == last.get("token")
        and first.get("from") == last.get("to")
        and float(last.get("amount", 0)) >= float(first.get("amount", 0))
    )


def analyse_trade(tx_data: dict, price_gap_bps: float = DEFAULT_PRICE_GAP_BPS) -> Analysis:
    """Full diagnostic. is_suspicious_trade() is a thin wrapper over this."""
    base = tx_data.get("base_token", "WBNB")
    quote = tx_data.get("quote_token", "USDT")
    a = Analysis(flashloan=_has_flashloan(tx_data))
    legs = _legs(tx_data)

    propamm_legs = [l for l in legs if l.protocol == PROPAMM and l.price(base, quote) is not None]
    if not propamm_legs:
        a.reasons.append("no PropAMM swap on the base/quote pair")
        return a

    # Find a PropAMM leg with a reverse leg on another venue, in either order.
    pair = None
    for pa in propamm_legs:
        for other in legs:
            if other.protocol == PROPAMM or other.price(base, quote) is None:
                continue
            if _chained(pa, other) or _chained(other, pa):
                pair = (pa, other)
                break
        if pair:
            break

    if pair is None:
        a.reasons.append("no reverse leg on another venue chained to the PropAMM swap")
        return a

    pa, other = pair
    a.round_trip = True
    a.propamm_price = pa.price(base, quote)
    a.other_price = other.price(base, quote)
    a.reference_price = float(tx_data["reference_price"]) if tx_data.get("reference_price") else a.other_price

    # Pool loses when it sells base below the reference or buys base above it.
    if pa.sells_base(base):
        disadvantage = (a.reference_price - a.propamm_price) / a.reference_price
    else:
        disadvantage = (a.propamm_price - a.reference_price) / a.reference_price
    a.pool_disadvantage_bps = disadvantage * 1e4

    first, second = (pa, other) if pa.index < other.index else (other, pa)
    if first.token_in == quote:
        a.trader_profit_quote = second.amount_out - first.amount_in
    else:  # started with base; express profit in quote at the reference
        a.trader_profit_quote = (second.amount_out - first.amount_in) * a.reference_price

    if a.pool_disadvantage_bps >= price_gap_bps:
        a.suspicious = True
        a.reasons.append(
            f"PropAMM leg filled {a.pool_disadvantage_bps:.1f} bps against the pool "
            f"(PropAMM {a.propamm_price:.2f} vs reference {a.reference_price:.2f}, "
            f"threshold {price_gap_bps:.0f} bps)"
        )
        if a.flashloan:
            a.reasons.append("flashloan bracket present: capital-free, atomic execution")
    else:
        a.reasons.append(
            f"round trip present but PropAMM leg within {price_gap_bps:.0f} bps of reference "
            f"({a.pool_disadvantage_bps:.1f} bps)"
        )
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
    print(f"trader profit        : {result.trader_profit_quote:.3f} USDT")
    for r in result.reasons:
        print(f"  - {r}")
