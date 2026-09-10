# PropAMM Quant Developer Take-Home

Parts 1 and 4 were selected per the assignment email.

| Part | Deliverables | Status |
|---|---|---|
| 1 - Pricing model | [Code](part1_pricing/pricing.py) / [Model & results](part1_pricing/MODEL.md) | Implemented |
| 4 - System design | [Design](part4_system_design/DESIGN.md) | Draft skeleton |

## Run Part 1

Python 3.10+, no dependencies. From the repository root:

```sh
python part1_pricing/pricing.py
python -m unittest discover -s part1_pricing -p "test_*.py" -v
```

## Part 1 summary

After input fees, rebalancing trades fill at oracle price $P$; the remainder follows
an inventory-dependent constant-product curve. Balance means $Px=y$. Virtual
reserves are rebuilt after Stable, fees are separate, and real reserves limit output.
Official cases use zero fees. [Model & results](part1_pricing/MODEL.md) contains
assumptions A1-A10, derivations, and both swap directions.
