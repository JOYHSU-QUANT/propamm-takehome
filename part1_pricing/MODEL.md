# Part 1 - Pricing Model

[Project README](../README.md) / [Implementation](pricing.py) / [Results](#results)

## Quote flow

The branches show the three execution cases; the implementation expresses them
with `stable_in = min(net_in, capacity)` and zero output for a zero-input phase.

```mermaid
flowchart TD
    Start[Validate inputs and deduct fee] --> Capacity[Compute stable capacity]
    Capacity --> Rebalance{"Rebalancing direction?<br/>capacity > 0"}
    Rebalance -->|No| CurveOnly[All net input on Curve]
    Rebalance -->|Yes| Fits{"Net input fits<br/>stable capacity?"}
    Fits -->|Yes| StableOnly[All net input at oracle P]
    Fits -->|No| StableFirst[Stable up to balance<br/>Update reserves]
    StableFirst --> CurveRest[Remainder on Curve<br/>using updated reserves]
    CurveOnly --> Output[Sum outputs and check real liquidity]
    StableOnly --> Output
    CurveRest --> Output
    Output --> Price[Compute and validate prices]
    Price --> Return([Return output, effective price, fee])
```

Invalid inputs, insufficient real liquidity, or invalid computed values reject the quote.

## Model assumptions

Identifiers match the implementation's docstring.

- **A1-A2:** Balance means $Px=y$; only rebalancing input receives the flat price $P$.
- **A3:** Rebuild virtual reserves after the stable phase; a crossing trade enters the
  curve at $P$. The assignment leaves this transition unspecified.
- **A4-A5:** Deduct input fees once; effective price includes fees and uses Y/X.
- **A6-A7:** Reject output reaching the real reserve; no partial fills. Bid/ask exclude fees.
- **A8:** Official examples use zero fees because no rate is given; 5 bps is illustrative.
- **A9:** Fees are separate from pricing reserves. Calls rebuild virtual reserves and
  persist no state. For fillable same-direction splits with fixed $P$, $\alpha$, and
  fee rate, update reserves using net input: splitting cannot increase output.
  Output is strictly lower only if $\alpha>1$ and at least two pieces have positive
  curve input; otherwise equal. This statement ignores floating-point rounding.
- **A10:** Finite numeric inputs; $x,y,P,a>0$, $\alpha\ge1$, $0\le b<10^4$;
  boolean direction. Human token units and floats; on-chain rounding is out of scope.

## Pricing equations

| Symbol | Meaning | Unit |
|---|---|---|
| $x,y$ | Real reserves before the swap | Token X, token Y |
| $x_s,y_s$ | Real reserves after the stable phase | Token X, token Y |
| $x_t,y_t$ | Real reserves at a point during the curve phase | Token X, token Y |
| $v_x,v_y$ | Virtual reserves, fixed during the curve phase | Token X, token Y |
| $X,Y$ | Effective reserves at the start of the curve phase | Token X, token Y |
| $\alpha$ | Concentration factor | Dimensionless |
| $P$ | Oracle price | Y per X |
| $p_{\mathrm{eff}}$ | All-in effective price | Y per X |
| $p_c$ | Curve-implied marginal price before the swap | Y per X |
| $a$ | Gross input (`amount_in`) | Input token |
| $b$ | Fee rate (`fee_bps`) | Basis points |
| $f$ | Fee charged (`fee_charged`) | Input token |
| $q$ | Input available for pricing after deducting fees (`net_in`) | Input token |
| $c$ | Maximum input executable at oracle $P$ before reaching balance (`capacity`); zero if the direction does not improve balance | Input token |
| $s,r$ | Stable input, remaining curve input | Input token |
| $o_s,o_c,o$ | Stable output, curve output, total output | Output token |
| $L^2$ | Curve invariant | Token X $\times$ token Y |

For X to Y, input is token X and output is token Y; the reverse swap exchanges these units.

### Input allocation

$$
f=a\frac{b}{10^4},\qquad q=a-f,\qquad s=\min(q,c),\qquad r=q-s.
$$

These equations cover all three paths in the flowchart:

| Execution path | Condition | Stable input $s$ | Curve input $r$ |
|---|---|---|---|
| Curve only | $c=0$ | $0$ | $q$ |
| Stable only | $0<q\le c$ | $q$ | $0$ |
| Stable then Curve | $q>c>0$ | $c$ | $q-c$ |

For Curve only, $o_s=0$ and the curve uses the original reserves ($x_s=x$, $y_s=y$).
For Stable only, $o_c=0$. For the mixed path, the curve uses reserves updated by
the stable phase. All paths return total output $o=o_s+o_c$.

`net_in` comes from the trader's input and fee; `capacity` comes from the pool's
reserves, oracle price, and trade direction. For example, in case E with zero fees,
`net_in = 20,000` USDT and `capacity = 12,620` USDT: 12,620 goes to Stable and
the remaining 7,380 goes to Curve.

Stable output at the oracle price is:

$$
o_s=\begin{cases}sP,&X\to Y,\\s/P,&Y\to X.\end{cases}
$$

### Stable capacity

At capacity, the post-trade reserves have equal oracle value. For Y to X,
input $s$ adds $s$ Y and removes $s/P$ X; for X to Y, it adds $s$ X and removes $Ps$ Y:

$$
\begin{aligned}
Y\to X:\quad & P\left(x-\frac{s}{P}\right)=y+s
&&\Longrightarrow\quad s=\frac{Px-y}{2},\\
X\to Y:\quad & P(x+s)=y-Ps
&&\Longrightarrow\quad s=\frac{y-Px}{2P}.
\end{aligned}
$$

Each trade reduces one side's oracle value and increases the other by the same
amount, closing the value gap by twice the traded value. Capacity takes the positive
part of these solutions; a non-rebalancing direction has zero stable capacity.

| Direction | Stable capacity $c$ |
|---|---|
| X to Y | $\max\left(0,\frac{y-Px}{2P}\right)$ |
| Y to X | $\max\left(0,\frac{Px-y}{2}\right)$ |

### Curve construction

$$
v_x=(\alpha-1)x_s,\quad v_y=(\alpha-1)y_s,\qquad
X=x_s+v_x=\alpha x_s,\quad Y=y_s+v_y=\alpha y_s.
$$

$$
(x_t+v_x)(y_t+v_y)=L^2=XY.
$$

### Curve output

Virtual reserves stay fixed, so input $r$ increases the input-side effective
reserve by $r$, while output $o_c$ reduces the other side by $o_c$.
Keeping their product equal to $XY$ gives:

$$
\begin{aligned}
Y\to X:\quad &(X-o_c)(Y+r)=XY
&&\Longrightarrow\quad o_c=X-\frac{XY}{Y+r}=\frac{Xr}{Y+r},\\
X\to Y:\quad &(X+r)(Y-o_c)=XY
&&\Longrightarrow\quad o_c=Y-\frac{XY}{X+r}=\frac{Yr}{X+r}.
\end{aligned}
$$

Here $r$ is the remaining net input after the stable phase; if $r=0$, then $o_c=0$.
The implementation uses the final fractional forms to avoid subtracting nearly
equal values, and separately checks that real reserves can pay the total output.

Total output and all-in effective price are:

$$
o=o_s+o_c,\qquad
p_{\mathrm{eff}}=\begin{cases}o/a,&X\to Y,\\a/o,&Y\to X.\end{cases}
$$

Both effective prices use gross input and are expressed in Y per X.

### Bid / ask

At the start of a quote, the pre-fee marginal prices are:

$$
p_c=\frac{\alpha y}{\alpha x}=\frac{y}{x},\qquad
\mathrm{bid}=\min(P,p_c),\qquad \mathrm{ask}=\max(P,p_c).
$$

Alpha changes depth, not the starting price. `get_quote_detailed` also reports
phase amounts and `reserve_ratio_after`: the final real Y/X ratio excluding fees,
not the endpoint price of the fixed-virtual-reserve curve.

<!-- BEGIN GENERATED RESULTS -->

## Results

All swaps are Y -> X at oracle P = 627 USDT/WBNB. Prices are in USDT/WBNB;
stable_capacity, stable_in, and curve_in are in USDT. fee is the charged amount, not the fee rate.

### Official cases A-D

Gross input: 500 USDT per case.

#### fee_bps = 0

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 500.00 | 0.791262 | 631.9020 | 0.0000 |
| B | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 0.00 | 0.00 | 500.00 | 0.529870 | 943.6275 | 0.0000 |
| C | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.00 | 500.00 | 0.00 | 0.797448 | 627.0000 | 0.0000 |
| D | balanced | 1.05 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 500.00 | 0.791437 | 631.7619 | 0.0000 |

#### fee_bps = 5

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 499.75 | 0.790869 | 632.2156 | 0.2500 |
| B | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 0.00 | 0.00 | 499.75 | 0.529607 | 944.0964 | 0.2500 |
| C | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.00 | 499.75 | 0.00 | 0.797049 | 627.3137 | 0.2500 |
| D | balanced | 1.05 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 499.75 | 0.791045 | 632.0756 | 0.2500 |

### Crossing case E

Case C reserves; gross input: 20,000 USDT.

#### fee_bps = 0

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out (WBNB) | eff. price | fee (USDT) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.00 | 12,620.00 | 7,380.00 | 30.678809 | 651.9158 | 0.0000 |

### Sanity checks / observations

- PASS: Case C executes entirely at P (eff. price == 627, curve_in == 0)
- PASS: Case D (alpha 1.05) returns more WBNB than Case A (alpha 1.02)
- PASS: Case E splits: 12,620 USDT stable, 7,380 USDT curve
- PASS: Effective price >= marginal ask for every Y -> X case
- PASS: X -> Y on Y-heavy pool (Case B reserves) executes at P
- PASS: Oversized trade raises InsufficientLiquidity instead of clamping

<!-- END GENERATED RESULTS -->

- **B:** Ask is 937.5 versus oracle 627, as implied by the reserve ratio. Production
  controls could bound deviation, suspend quotes, or rebalance pool inventory.
  Binance hedging alone does not change pool reserves.
- **A vs D:** Effective depth is 2% vs 5% above $\alpha=1$ (D is 2.94% deeper than A).
  Output rises by 0.000175 WBNB; slippage falls from 78.18 to 75.95 bps.
- **Splitting:** A split into two 250 USDT trades loses about 0.0000614 WBNB.
  E split at or before its stable boundary has unchanged output; splitting
  15,000 + 5,000 USDT lowers output from 30.678809 to 30.669075 WBNB (zero fees).

[Tests](test_pricing.py) cover both directions, fees, invariants, boundary continuity,
input validation, reserve exhaustion, and splitting.

Refresh the result tables and sanity checks in this document from the repository root:

```sh
python part1_pricing/pricing.py --write-results
```
