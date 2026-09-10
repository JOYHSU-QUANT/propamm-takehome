# PropAMM Quant Developer Take-Home

Parts 1 and 3 were selected per the assignment email.

| Part | Deliverables | Status |
|---|---|---|
| 1 - Pricing model | [Code](part1_pricing/pricing.py) / [Model & results](part1_pricing/MODEL.md) | Implemented |
| 3 - Arb detection & defence | [Writeup](part3_arb_defence/DEFENCE.md) / [Code](part3_arb_defence/arb_detection.py) | Implemented |

## Run

Python 3.10+, no dependencies. From the repository root:

```sh
python part1_pricing/pricing.py                                   # Part 1 result tables
python part3_arb_defence/arb_detection.py                         # Part 3 analysis of the observed attack
python -m unittest discover -s part1_pricing -p "test_*.py" -v    # Part 1 tests
python -m unittest discover -s part3_arb_defence -p "test_*.py" -v  # Part 3 tests
```

## Part 1 summary

After input fees, rebalancing trades fill at oracle price $P$; the remainder follows
an inventory-dependent constant-product curve. Balance means $Px=y$. Virtual
reserves are rebuilt after Stable, fees are separate, and real reserves limit output.
Official cases use zero fees. [Model & results](part1_pricing/MODEL.md) contains
assumptions A1-A10, derivations, and both swap directions.

## Part 3 summary

The attack worked because the pool's no-arbitrage guarantee ($\mathrm{bid}\le P\le\mathrm{ask}$)
is only relative to its own oracle; with $P$ 6% below market, the ask was a 6% discount.
`is_suspicious_trade` flags a same-transaction round trip through the PropAMM whose
PropAMM leg filled more than 30 bps against the pool versus a reference price; a
flashloan raises severity but is not required. The defence is an on-chain expiry plus a
deviation bound against an independent reference; the expiry is derived from refresh
cadence, the 2 s WebSocket silence and inclusion latency: 5 blocks (BSC) and 3 (Base)
at every-block refresh, 20 and 8 at the current 9.6 s cadence.
[Writeup](part3_arb_defence/DEFENCE.md) has the diagnosis, heuristics, pseudocode and working.
