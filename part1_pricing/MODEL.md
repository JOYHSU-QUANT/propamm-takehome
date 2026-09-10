# Part 1 - Pricing Model

[Project README](../README.md) / [Implementation](pricing.py) / [Results](results.md)

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

## Pricing equations

Let $x,y$ be real reserves, $P$ the oracle price in Y per X, $a$ gross input,
$b$ the fee in basis points, and $q$ net input:

$$
f=a\frac{b}{10^4},\qquad q=a-f.
$$

Stable input is $s=\min(q,c)$, where $c$ is the capacity below; remaining curve
input is $r=q-s$. After the stable phase, build effective reserves from $x_s,y_s$:

$$
v_x=(\alpha-1)x_s,\quad v_y=(\alpha-1)y_s,\qquad
X=x_s+v_x=\alpha x_s,\quad Y=y_s+v_y=\alpha y_s.
$$

Hold $v_x,v_y$ fixed during the curve phase, with invariant
$(x+v_x)(y+v_y)=L^2=XY$. Let $o=o_s+o_c$ be total output.

| Quantity | X to Y | Y to X |
|---|---|---|
| Stable capacity $c$ | $\max\left(0,\frac{y-Px}{2P}\right)$ | $\max\left(0,\frac{Px-y}{2}\right)$ |
| Stable output $o_s$ | $sP$ | $s/P$ |
| Curve output $o_c$ | $\frac{Yr}{X+r}$ | $\frac{Xr}{Y+r}$ |
| All-in effective price (Y/X) | $o/a$ | $a/o$ |

For example, Y to X reaches balance when $P(x-s/P)=y+s$, giving
$c=(Px-y)/2$ when positive. The curve equation $(X-o_c)(Y+r)=XY$
gives $o_c=Xr/(Y+r)$.

At the start of a quote, the pre-fee marginal prices are:

$$
p_c=\frac{\alpha y}{\alpha x}=\frac{y}{x},\qquad
\mathrm{bid}=\min(P,p_c),\qquad \mathrm{ask}=\max(P,p_c).
$$

Alpha changes depth, not the starting price. `get_quote_detailed` also reports
phase amounts and `reserve_ratio_after`: the final real Y/X ratio excluding fees,
not the endpoint price of the fixed-virtual-reserve curve.

## Assumptions

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

## Results and checks

- **B:** Ask is 937.5 versus oracle 627, as implied by the reserve ratio. Production
  controls could bound deviation, suspend quotes, or rebalance pool inventory.
  Binance hedging alone does not change pool reserves.
- **A vs D:** Effective depth is 2% vs 5% above $\alpha=1$ (D is 2.94% deeper than A).
  Output rises by 0.000175 WBNB; slippage falls from 78.18 to 75.95 bps.
- **Splitting:** A split into two 250 USDT trades loses about 0.0000614 WBNB.
  E split at or before its stable boundary has unchanged output; splitting
  15,000 + 5,000 USDT lowers output from 30.678809 to 30.669075 WBNB (zero fees).

[Tests](test_pricing.py) cover both directions, fees, invariants, boundary continuity,
input validation, reserve exhaustion, and splitting. [Tables](results.md) contain A-E.

Regenerate tables from the repository root:

```sh
python -c "from pathlib import Path; from part1_pricing.pricing import main; Path('part1_pricing/results.md').write_text(main(), encoding='utf-8')"
```
