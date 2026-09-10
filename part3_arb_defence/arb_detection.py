"""
Part 3 - Flashloan arb detection.

is_suspicious_trade(tx_data) returns True when a transaction's transfer log
matches a flashloan arbitrage against the PropAMM. Field definitions,
heuristics and rationale are in DEFENCE.md, section "Detect".

tx_data is a dict with:
  transfers:     list of {token, from, to, amount}            ERC-20 Transfer events, in order
  swaps:         list of {protocol, token_in, token_out, amount_in, amount_out}
  interactions:  list of protocol labels touched, in order    e.g. ["ListaDAO", "PropAMM", "PancakeV2"]
  timestamp:     unix seconds of the block
  block_number:  int
"""

from __future__ import annotations


def is_suspicious_trade(tx_data: dict) -> bool:
    """Return True if the trade pattern matches a flashloan arb against the PropAMM."""
    raise NotImplementedError


# Observed transaction from the assignment, used as the positive test case.
OBSERVED_ATTACK = {
    "transfers": [],
    "swaps": [
        {"protocol": "PropAMM", "token_in": "USDT", "token_out": "WBNB", "amount_in": 10.0, "amount_out": 0.01702},
        {"protocol": "PancakeV2", "token_in": "WBNB", "token_out": "USDT", "amount_in": 0.01702, "amount_out": 10.627},
    ],
    "interactions": ["ListaDAO", "PropAMM", "PancakeV2", "ListaDAO"],
    "timestamp": 0,
    "block_number": 0,
}


if __name__ == "__main__":
    print("suspicious:", is_suspicious_trade(OBSERVED_ATTACK))
