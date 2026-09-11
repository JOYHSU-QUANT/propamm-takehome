# Part 1 - Pricing Model

[Project README](../README.md) / [Implementation](pricing.py) / [Results](#results)

## Quote flow

Stable input is capped at the amount needed to reach balance; any remainder goes to Curve.

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

A1-A10 match the implementation's inline references.

| ID | Assumption | Why |
|---|---|---|
| A1 | Balance means equal oracle value: $Px=y$. | The stable phase exists to cut inventory risk, and that risk is denominated in value, not token units. |
| A2 | Only rebalancing input fills at $P$, up to balance. | Flow that worsens the imbalance should pay the curve's inventory-dependent price, not the flat one. |
| A3 | Rebuild virtual reserves after Stable; crossing trades enter Curve at $P$. This transition is a modeling choice. | The pool is balanced at the boundary, so the curve starts exactly at $P$ and pricing is continuous. The assignment leaves the transition unspecified. |
| A4 | Deduct the fee once from input; price only the net amount. | Standard AMM convention; keeps fee accounting independent of the two pricing phases. |
| A5 | Effective price includes fees and always uses Y per X. | The all-in price is what the trader experiences; a fixed unit makes both directions directly comparable. |
| A6 | Reject output reaching the real reserve. Virtual liquidity is not transferable; no clamping or partial fills. | Clamping would silently change the execution price. Virtual reserves shape depth but cannot be withdrawn. |
| A7 | Bid/ask are pre-fee marginal prices. | The requested `get_bid_ask` signature has no fee argument; fees and finite-size slippage belong to `get_quote`. |
| A8 | Official cases use zero fees because none are specified; 5 bps is illustrative. | Zero fee isolates the pricing model. The 5 bps *spread* in Part 2 is not a fee and is not assumed to be one. |
| A9 | Quotes persist no state and rebuild virtual reserves per call. Fees stay outside pricing reserves. | Matches the function signatures, which take reserves as input. Consequence: splitting a same-direction trade never increases output (strictly lower only if $\alpha>1$ and two or more pieces each carry curve input); tested at 0, 5 and 30 bps. |
| A10 | Finite values; $x,y,P,a>0$, $\alpha\ge1$, $0\le b<10^4$; boolean direction. Floats in human token units; on-chain rounding is out of scope. | Keeps the exercise focused on pricing logic rather than fixed-point arithmetic. |

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
| $q$ | Net input after fees (`net_in`) | Input token |
| $c$ | Stable input limit (`capacity`); zero for non-rebalancing flow | Input token |
| $s,r$ | Stable input, remaining curve input | Input token |
| $o_s,o_c,o$ | Stable output, curve output, total output | Output token |
| $L^2$ | Curve invariant | Token X $\times$ token Y |

For X to Y, input is token X and output is token Y; the reverse swap exchanges these units.

### Input

**Fees and net input**

```math
f=a\frac{b}{10^4},\qquad q=a-f.
```

`net_in` depends on trade size and fees; `capacity` depends on reserves, $P$, and direction.

**Stable capacity**

For a rebalancing trade, the stable limit ($s=c$) satisfies $P x_s=y_s$.
After stable input $s$, the reserves are:

| Swap direction | X reserve $x_s$ | Y reserve $y_s$ |
|---|---|---|
| Y to X | $x-\frac{s}{P}$ | $y+s$ |
| X to Y | $x+s$ | $y-Ps$ |

Substituting into the balance condition and solving for $s$:

```math
\begin{aligned}
Y\to X:\quad & P\left(x-\frac{s}{P}\right)=y+s
&&\Longrightarrow\quad s=\frac{Px-y}{2},\\
X\to Y:\quad & P(x+s)=y-Ps
&&\Longrightarrow\quad s=\frac{y-Px}{2P}.
\end{aligned}
```

Both sides change by the same oracle value, so the gap closes by twice that amount.
Take the positive part of the solution; non-rebalancing flow has zero capacity.

| Direction | Stable capacity $c$ |
|---|---|
| X to Y | $\max\left(0,\frac{y-Px}{2P}\right)$ |
| Y to X | $\max\left(0,\frac{Px-y}{2}\right)$ |

**Input allocation**

```math
s=\min(q,c),\qquad r=q-s.
```

These equations cover all three paths in the flowchart:

| Execution path | Condition | Stable input $s$ | Curve input $r$ |
|---|---|---|---|
| Curve only | $c=0$ | $0$ | $q$ |
| Stable only | $0<q\le c$ | $q$ | $0$ |
| Stable then Curve | $q>c>0$ | $c$ | $q-c$ |

Curve uses the reserves after Stable, or the original reserves when $s=0$.
A phase with zero input contributes zero output.

Case E: 20,000 USDT net input minus 12,620 Stable capacity leaves 7,380 for Curve.

### Output

**Stable output**

At the oracle price:

```math
o_s = \begin{cases}
sP, & X \to Y, \\
\frac{s}{P}, & Y \to X.
\end{cases}
```

**Curve construction**

```math
v_x=(\alpha-1)x_s,\quad v_y=(\alpha-1)y_s,\qquad
X=x_s+v_x=\alpha x_s,\quad Y=y_s+v_y=\alpha y_s.
```

```math
(x_t+v_x)(y_t+v_y)=L^2=XY.
```

**Curve output**

With virtual reserves fixed, add $r$ to the input side and subtract $o_c$ from
the output side, preserving the product $XY$:

```math
\begin{aligned}
Y\to X:\quad &(X-o_c)(Y+r)=XY
&&\Longrightarrow\quad o_c=X-\frac{XY}{Y+r}=\frac{Xr}{Y+r},\\
X\to Y:\quad &(X+r)(Y-o_c)=XY
&&\Longrightarrow\quad o_c=Y-\frac{XY}{X+r}=\frac{Yr}{X+r}.
\end{aligned}
```

The implementation uses the final fractions to avoid subtracting nearly equal values.
Real-reserve limits are checked separately (A6).

**Total output by execution mode**

Here $q$ is net input and $c$ is stable capacity. Each entry is total output $o$:

| Mode | Condition | X to Y: output in Y | Y to X: output in X |
|---|---|---|---|
| Curve only | $c=0$ | $\frac{Yq}{X+q}$ | $\frac{Xq}{Y+q}$ |
| Stable only | $0<q\le c$ | $qP$ | $q/P$ |
| Stable then Curve | $q>c>0$ | $cP+\frac{Y(q-c)}{X+q-c}$ | $\frac{c}{P}+\frac{X(q-c)}{Y+q-c}$ |

For Curve only, $X=\alpha x$ and $Y=\alpha y$. For the mixed mode, use the
post-Stable reserves: $X=\alpha x_s$, $Y=\alpha y_s$, where
$(x_s,y_s)=(x+c,y-Pc)$ for X to Y, or $(x-c/P,y+c)$ for Y to X.

**Effective price**

Using gross input, in Y per X:

```math
o=o_s+o_c,\qquad
p_{\mathrm{eff}} = \begin{cases}
\frac{o}{a}, & X \to Y, \\
\frac{a}{o}, & Y \to X.
\end{cases}
```

### Bid and Ask

At the start of a quote, the pre-fee marginal prices are:

```math
p_c=\frac{\alpha y}{\alpha x}=\frac{y}{x},\qquad
\mathrm{bid}=\min(P,p_c),\qquad \mathrm{ask}=\max(P,p_c).
```

Alpha changes depth, not the starting price. `get_quote_detailed` also reports
phase amounts and `reserve_ratio_after`: the final real Y/X ratio excluding fees,
not the endpoint price of the fixed-virtual-reserve curve.

At balance, Part 1 intentionally returns $\mathrm{bid}=\mathrm{ask}=P$: the Stable
phase is specified to execute at the flat oracle price, and `get_bid_ask` has no
spread input. Part 2's 5 bps is an exogenous production-spread assumption for refresh
economics. Part 1 charges `fee_bps`; Part 3 studies loss from oracle staleness.

**No arbitrage against the oracle.** With $P$ equal to the external market price,
$\mathrm{bid}\le P\le\mathrm{ask}$ means the pool never quotes a trader better than $P$
in either direction, and a finite trade only adds slippage on top. A zero-fee round
trip therefore always loses: 500 USDT bought and immediately sold back returns
496.0 to 496.2 USDT on Case A reserves and about 332 and 336 USDT on B and C;
1 WBNB round-tripped returns 0.990, 0.677 and 0.659 WBNB (`test_round_trip_never_profits`).
This assumes valid fills and excludes implementation faults. A stale $P$ removes the
protection; [Part 3](../part3_arb_defence/DEFENCE.md) analyses that case.

<!-- BEGIN GENERATED RESULTS -->

## Results

P = 627 USDT/WBNB; all prices use USDT/WBNB. Capacity, phase inputs, and fee
use the input token; amount_out uses the output token. Fee rate appears only in headings.

### Official cases A-D

USDT -> WBNB; gross input: 500 USDT per case.

#### fee_bps = 0

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 500.00 | 0.791262 | 631.9020 | 0.0000 |
| B | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 0.00 | 0.00 | 500.00 | 0.529870 | 943.6275 | 0.0000 |
| C | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.00 | 500.00 | 0.00 | 0.797448 | 627.0000 | 0.0000 |
| D | balanced | 1.05 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 500.00 | 0.791437 | 631.7619 | 0.0000 |

#### fee_bps = 5

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 499.75 | 0.790869 | 632.2156 | 0.2500 |
| B | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 0.00 | 0.00 | 499.75 | 0.529607 | 944.0964 | 0.2500 |
| C | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.00 | 499.75 | 0.00 | 0.797049 | 627.3137 | 0.2500 |
| D | balanced | 1.05 | 627.00 | 627.00 | 627.00 | 0.00 | 0.00 | 499.75 | 0.791045 | 632.0756 | 0.2500 |

### Crossing case E

Case C reserves; USDT -> WBNB; gross input: 20,000 USDT.

#### fee_bps = 0

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.00 | 12,620.00 | 7,380.00 | 30.678809 | 651.9158 | 0.0000 |

### Reverse-direction cases F-G

WBNB -> USDT; gross input: 1 WBNB. F uses Case B reserves; G uses Case A reserves.

#### fee_bps = 0

| Case | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 19.8086 | 1.0000 | 0.0000 | 627.000000 | 627.0000 | 0.0000 |
| G | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.0000 | 0.0000 | 1.0000 | 620.912621 | 620.9126 | 0.0000 |

### Sanity checks / observations

- PASS: C: entirely Stable at P = 627.
- PASS: D returns more WBNB than A as alpha increases.
- PASS: E: 12,620 USDT Stable + 7,380 USDT Curve.
- PASS: F: entirely Stable at P = 627.
- PASS: A-G: effective prices respect marginal bid/ask bounds.
- PASS: A-C reserves: tested zero-fee round trips return less than 500 USDT.
- PASS: Oversized input raises InsufficientLiquidity.

<!-- END GENERATED RESULTS -->

- **B:** Ask 937.5 versus oracle 627 is model-consistent: $\alpha$ scales both
  effective reserves, so it changes depth but not the starting price. Production
  policy: if $|p_c/P-1|$ exceeds the configured inventory-skew limit, suppress the
  inventory-worsening quote and rebalance the pool. Binance hedging does not restore
  pool reserves.
- **A vs D:** D is 2.94% deeper; output rises by 0.000175 WBNB and slippage falls
  from 78.18 to 75.95 bps.
- **F vs G:** Rebalancing sells fill at 627 (F); sells into a balanced pool take
  the curve price, 620.91 (G).
- **Splitting:** For fillable same-direction trades with fixed $P$, $\alpha$, and fee
  rate, updating reserves by net input after each fill, splitting cannot increase output
  (ignoring rounding). It strictly reduces output only when $\alpha>1$ and two or more
  pieces have positive curve input; otherwise output is equal. Case A loses about
  0.0000614 WBNB when split in half; E is unchanged when split at its Stable boundary.

[Tests](test_pricing.py) cover both directions, fees, invariants, boundary continuity,
input validation, reserve exhaustion, splitting, and round trips.

Refresh the result tables and sanity checks in this document from the repository root:

```sh
python part1_pricing/pricing.py --write-results
```
