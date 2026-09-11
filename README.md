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

A stale oracle let the trader buy below market and earn 0.627 USDT before loan fees
and gas. The detector flags reverse swap pairs with at least 30 bps disadvantage
against a reference; flashloan funding is optional. A rolling monitor covers slower
extraction. Defence checks source age, block age, independent price deviation, and a
block-level loss budget. Every-block refresh gives expiry budgets of
6 BSC blocks and 4 Base blocks; slower schedules require recalculation.
[Writeup](part3_arb_defence/DEFENCE.md) contains the assumptions and derivation.
