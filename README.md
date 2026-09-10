# PropAMM Quant Developer Take-Home

Parts 1 and 4 were selected per the assignment email.

| Part | Deliverables | Status |
|---|---|---|
| 1 — Pricing model | [Code](part1_pricing/pricing.py) · [Model notes & flowchart](part1_pricing/MODEL.md) · [Results](part1_pricing/results.md) | Implemented |
| 4 — System design | [Design](part4_system_design/DESIGN.md) | Draft skeleton |

## Run Part 1

Python 3.10+, standard library only. From the repository root:

```sh
python part1_pricing/pricing.py
python -m unittest discover -s part1_pricing -p "test_*.py" -v
```

## Pricing approach

After deducting the input fee, trades that move the pool toward equal oracle
value execute at the oracle price `P`. Remaining input follows a concentrated
constant-product curve. `get_quote` returns output, all-in price in Y per X,
and the input-token fee; `get_bid_ask` returns pre-fee marginal prices.

Key assumptions: 50/50 means `P*x = y`; virtual reserves are rebuilt after the
stable phase and held fixed during the curve phase; fees are accounted for
separately. Quotes that exhaust real reserves are rejected. Official examples
use zero fees because the assignment does not specify a fee rate.

See the [model notes](part1_pricing/MODEL.md) for formulas, the implementation
flowchart, full assumptions, and test observations.
