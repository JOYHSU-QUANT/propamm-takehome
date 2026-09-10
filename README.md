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

After input fees, the part of a trade that moves the pool toward 50/50 (in oracle
value) fills at the oracle price $P$; the remainder fills on a concentrated
constant-product curve built from the post-stable reserves. The curve's marginal
price is simply $y/x$, so $\mathrm{bid}=\min(P,y/x)$ and $\mathrm{ask}=\max(P,y/x)$,
and the pool never quotes better than the oracle.

Assumptions, one line each (reasoning and derivations in [MODEL.md](part1_pricing/MODEL.md)):

- **A1** Balance means equal oracle value, $Px=y$.
- **A2** Only rebalancing flow fills at $P$; the rest goes to the curve.
- **A3** Virtual reserves are rebuilt after the stable phase, so the curve starts at $P$.
- **A4** Fee is taken once from the input token; only net input is priced.
- **A5** Effective price is all-in (fee included) and always in Y per X.
- **A6** Output that would exhaust the real reserve is rejected, not clamped.
- **A7** Bid/ask are pre-fee marginal prices.
- **A8** Official cases use zero fee because none is specified; 5 bps is illustrative.
- **A9** Quotes are stateless; splitting a trade never increases total output.
- **A10** Inputs are finite floats in human token units; on-chain rounding is out of scope.
