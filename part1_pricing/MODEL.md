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

Identifiers match the inline comments in the implementation.

- **A1. Balance is measured in oracle value.** "50/50" means $Px=y$, not equal token
  counts. The stable phase exists to reduce the pool's inventory risk, and that risk
  is denominated in value, not units.
- **A2. Only rebalancing flow gets the flat price.** A trade enters the stable phase
  only if it moves the pool toward $Px=y$, and fills at $P$ until the pool is exactly
  balanced. Flow that worsens the imbalance goes straight to the curve.
- **A3. Virtual reserves are rebuilt after the stable phase.** A trade that crosses
  from stable to curve enters the curve with the pool at balance, so the curve starts
  exactly at $P$ and pricing is continuous at the boundary. The assignment does not
  specify this transition; it is an explicit modeling choice.
- **A4. Fee is taken once from the input token.** $f=a\cdot b/10^4$; only the net
  amount $q=a-f$ is priced.
- **A5. Effective price is all-in.** It is computed from gross input $a$ (fee included),
  and is always expressed in Y per X regardless of direction, so that quotes in the two
  directions are directly comparable.
- **A6. Reject, do not clamp, when output would exhaust the real reserve.** Virtual
  liquidity changes pricing depth but cannot create transferable tokens. Clamping
  output to the real reserve would silently change the execution price, so the quote
  raises `InsufficientLiquidity` instead. Partial fills are not modeled.
- **A7. Bid/ask are pre-fee marginal prices.** The requested `get_bid_ask` signature
  has no fee argument, so it reports the marginal price at zero trade size; fees and
  finite-size slippage appear only in `get_quote`.
- **A8. Official cases use zero fee.** The test table does not specify `fee_bps`, so
  the canonical results use 0 to isolate the pricing model. The 5 bps table is
  illustrative only; the 5 bps *spread* quoted in Part 2 is not a fee and is not
  assumed to be one.
- **A9. The quote is stateless and fees stay out of pricing reserves.** Each call
  rebuilds virtual reserves from the supplied state and persists no invariant across
  calls, so splitting a trade can change total output. For same-direction splits with
  fixed $P$, $\alpha$ and fee rate, reserves updated by net input after each fill, and
  both executions fillable, splitting never increases output (ignoring floating-point
  rounding). Output is strictly lower only if $\alpha>1$ and at least two pieces each
  carry positive curve input; otherwise it is equal. Case E split at or before its
  stable boundary is unchanged; Case A split into 2 x 250 USDT loses about 6.14e-5
  WBNB. A regression test covers this at 0, 5 and 30 bps.
- **A10. Inputs are finite floats in human token units.** $x,y,P,a>0$, $\alpha\ge1$,
  $0\le b<10^4$, boolean direction. Token decimals and on-chain integer rounding are
  out of scope.

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

**No arbitrage against the oracle.** By construction $\mathrm{bid}\le P\le\mathrm{ask}$,
so the pool never quotes a trader a price better than $P$ in either direction, and a
finite trade only adds slippage on top. A zero-fee round trip therefore always loses:
500 USDT bought and immediately sold back returns 496.0 to 496.2 USDT on Case A
reserves and about 332 and 336 USDT on B and C; 1 WBNB round-tripped returns 0.990,
0.677 and 0.659 WBNB. When the oracle equals the external market, an arbitrageur has
nothing to extract. The only way to profit against this pool is for $P$ itself to be
wrong, which is exactly the stale-oracle condition described in Part 3 of the
assignment.

<!-- BEGIN GENERATED RESULTS -->

## Results

Oracle P = 627 USDT/WBNB in every case. Prices (cpPrice, bid, ask, eff. price) are in USDT/WBNB.
stable_capacity, stable_in, curve_in, amount_in and fee are in the input token;
amount_out is in the output token. Y->X means the trader pays USDT and receives WBNB;
X->Y means the trader pays WBNB and receives USDT. fee is the charged amount, not the rate.

### Official cases A-D

Y->X, gross input 500 USDT per case.

#### fee_bps = 0

| Case | Direction | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | Y->X (USDT in) | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.0000 | 0.0000 | 500.0000 | 500.0000 | 0.791262 | 631.9020 | 0.0000 |
| B | Y->X (USDT in) | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 0.0000 | 0.0000 | 500.0000 | 500.0000 | 0.529870 | 943.6275 | 0.0000 |
| C | Y->X (USDT in) | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.0000 | 500.0000 | 0.0000 | 500.0000 | 0.797448 | 627.0000 | 0.0000 |
| D | Y->X (USDT in) | balanced | 1.05 | 627.00 | 627.00 | 627.00 | 0.0000 | 0.0000 | 500.0000 | 500.0000 | 0.791437 | 631.7619 | 0.0000 |

#### fee_bps = 5

| Case | Direction | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | Y->X (USDT in) | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.0000 | 0.0000 | 499.7500 | 500.0000 | 0.790869 | 632.2156 | 0.2500 |
| B | Y->X (USDT in) | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 0.0000 | 0.0000 | 499.7500 | 500.0000 | 0.529607 | 944.0964 | 0.2500 |
| C | Y->X (USDT in) | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.0000 | 499.7500 | 0.0000 | 500.0000 | 0.797049 | 627.3137 | 0.2500 |
| D | Y->X (USDT in) | balanced | 1.05 | 627.00 | 627.00 | 627.00 | 0.0000 | 0.0000 | 499.7500 | 500.0000 | 0.791045 | 632.0756 | 0.2500 |

### Crossing case E

Case C reserves, Y->X, gross input 20,000 USDT.

#### fee_bps = 0

| Case | Direction | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E | Y->X (USDT in) | X-heavy | 1.02 | 416.67 | 416.67 | 627.00 | 12,620.0000 | 12,620.0000 | 7,380.0000 | 20,000.0000 | 30.678809 | 651.9158 | 0.0000 |

### Reverse-direction cases F-G

X->Y, gross input 1 WBNB. F uses Case B reserves (Y-heavy), so selling WBNB rebalances
the pool and fills entirely at P. G uses Case A reserves (balanced), so it goes straight
to the curve and fills below P.

#### fee_bps = 0

| Case | Direction | Pool | alpha | cpPrice | bid | ask | stable_capacity | stable_in | curve_in | amount_in | amount_out | eff. price | fee |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F | X->Y (WBNB in) | Y-heavy | 1.02 | 937.50 | 627.00 | 937.50 | 19.8086 | 1.0000 | 0.0000 | 1.0000 | 627.000000 | 627.0000 | 0.0000 |
| G | X->Y (WBNB in) | balanced | 1.02 | 627.00 | 627.00 | 627.00 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 620.912621 | 620.9126 | 0.0000 |

### Sanity checks / observations

- PASS: Case C executes entirely at P (eff. price == 627, curve_in == 0)
- PASS: Case D (alpha 1.05) returns more WBNB than Case A (alpha 1.02)
- PASS: Case E splits: 12,620 USDT stable, 7,380 USDT curve
- PASS: Case F (X->Y on Y-heavy Case B reserves) executes at P
- PASS: Effective price never beats the marginal bid (X->Y) or ask (Y->X) in any case
- PASS: bid <= P <= ask; a zero-fee round trip 500 USDT -> WBNB -> USDT never profits
- PASS: Oversized trade raises InsufficientLiquidity instead of clamping

<!-- END GENERATED RESULTS -->

- **B:** Ask is 937.5 versus oracle 627, as implied by the reserve ratio. Production
  controls could bound deviation, suspend quotes, or rebalance pool inventory.
  Binance hedging alone does not change pool reserves.
- **A vs D:** Effective depth is 2% vs 5% above $\alpha=1$ (D is 2.94% deeper than A).
  Output rises by 0.000175 WBNB; slippage falls from 78.18 to 75.95 bps.
- **F vs G:** Same 1 WBNB sold, opposite outcomes. F's Y-heavy pool wants WBNB,
  so the sale rebalances and fills at $P=627$. G's balanced pool has no stable
  capacity, so the sale goes to the curve and fills at 620.91, below $P$. Together
  with A-D this shows both sides of the bid/ask rule.
- **Splitting:** A split into two 250 USDT trades loses about 0.0000614 WBNB.
  E split at or before its stable boundary has unchanged output; splitting
  15,000 + 5,000 USDT lowers output from 30.678809 to 30.669075 WBNB (zero fees).

[Tests](test_pricing.py) cover both directions, fees, invariants, boundary continuity,
input validation, reserve exhaustion, splitting, and round trips.

Refresh the result tables and sanity checks in this document from the repository root:

```sh
python part1_pricing/pricing.py --write-results
```
