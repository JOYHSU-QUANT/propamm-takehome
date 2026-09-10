# PropAMM Quant Developer Take-Home

Parts 1 and 4 were selected per the assignment email.

| Part | Deliverables | Status |
|---|---|---|
| 1 - Pricing model | [Code](part1_pricing/pricing.py) / [Model & flowchart](part1_pricing/MODEL.md) / [Results](part1_pricing/results.md) | Implemented |
| 4 - System design | [Design](part4_system_design/DESIGN.md) | Draft skeleton |

## Run Part 1

Python 3.10+, no dependencies. From the repository root:

```sh
python part1_pricing/pricing.py
python -m unittest discover -s part1_pricing -p "test_*.py" -v
```

## Model summary

After input fees, rebalancing trades execute at oracle price $P$; the remainder
uses a concentrated constant-product curve. Balance means $Px=y$. Virtual reserves
are rebuilt after the stable phase, fees are separate, and real reserves limit output.
Official examples use zero fees. [Model notes](part1_pricing/MODEL.md) contain the formulas and assumptions.
