# Part 1 - Test Results

### Official cases (fee_bps = 0, all Y -> X)

| Case | Pool | cpPrice | bid | ask | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |
|---|---|---|---|---|---|---|---|---|---|
| A | balanced | 627.00 | 627.00 | 627.00 | 0.00 | 500.00 | 0.791262 | 631.9020 | 0.0000 |
| B | Y-heavy | 937.50 | 627.00 | 937.50 | 0.00 | 500.00 | 0.529870 | 943.6275 | 0.0000 |
| C | X-heavy | 416.67 | 416.67 | 627.00 | 500.00 | 0.00 | 0.797448 | 627.0000 | 0.0000 |
| D | balanced | 627.00 | 627.00 | 627.00 | 0.00 | 500.00 | 0.791437 | 631.7619 | 0.0000 |

### Official cases with an illustrative fee (fee_bps = 5, all Y -> X)

| Case | Pool | cpPrice | bid | ask | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |
|---|---|---|---|---|---|---|---|---|---|
| A | balanced | 627.00 | 627.00 | 627.00 | 0.00 | 499.75 | 0.790869 | 632.2156 | 0.2500 |
| B | Y-heavy | 937.50 | 627.00 | 937.50 | 0.00 | 499.75 | 0.529607 | 944.0964 | 0.2500 |
| C | X-heavy | 416.67 | 416.67 | 627.00 | 499.75 | 0.00 | 0.797049 | 627.3137 | 0.2500 |
| D | balanced | 627.00 | 627.00 | 627.00 | 0.00 | 499.75 | 0.791045 | 632.0756 | 0.2500 |

### Extra case E: stable -> curve crossing (fee_bps = 0, all Y -> X)

| Case | Pool | cpPrice | bid | ask | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |
|---|---|---|---|---|---|---|---|---|---|
| E | X-heavy | 416.67 | 416.67 | 627.00 | 12,620.00 | 7,380.00 | 30.678809 | 651.9158 | 0.0000 |

### Sanity checks

- PASS: Case C executes entirely at P (eff. price == 627, curve_in == 0)
- PASS: Case D (alpha 1.05) returns more WBNB than Case A (alpha 1.02)
- PASS: Case E splits: 12,620 USDT stable, 7,380 USDT curve
- PASS: Effective price >= marginal ask for every Y -> X case
- PASS: X -> Y on Y-heavy pool (Case B reserves) executes at P
- PASS: Oversized trade raises InsufficientLiquidity instead of clamping
