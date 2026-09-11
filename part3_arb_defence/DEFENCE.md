# Part 3 - Flashloan Arb Detection & Defence

[Project README](../README.md) / [Implementation](arb_detection.py) / [Tests](test_arb_detection.py)

## 1. Diagnose

The trader borrowed 10 USDT from Lista DAO, bought 0.01702 WBNB from the PropAMM,
sold it on PancakeSwap V2 for 10.627 USDT, and repaid the loan.

| Metric | Calculation | Result |
|---|---|---|
| PropAMM fill | 10 / 0.01702 | 587.54 USDT/WBNB |
| PancakeSwap fill | 10.627 / 0.01702 | 624.38 USDT/WBNB |
| Gross profit | 10.627 - 10 | 0.627 USDT, before loan fee and gas |

The oracle was about 6% below market. The [Part 1 model](../part1_pricing/MODEL.md)
only guarantees prices relative to that oracle:

```math
\mathrm{bid} \le P \le \mathrm{ask}.
```

With a stale $P$, the pool's ask can remain below the external market, allowing an
immediate resale profit. The enabling condition was accepting that price without an
effective freshness or independent deviation guard.

Buying back the sold WBNB on Binance realises the same economic loss, plus hedge
execution costs. Flashloan funding makes the route atomic without upfront trading
capital; a funded trader can exploit the same gap. Curve slippage and available
inventory limit the profitable size. The assignment does not specify the stale
duration or the pool's reserves.

## 2. Detect

### Input contract

`is_suspicious_trade(tx_data)` consumes decoded events from one transaction.
The decoder supplies consistent token identifiers, normalized human token amounts,
and finite positive amounts and prices.

| Field | Contents |
|---|---|
| `swaps` | `{protocol, token_in, token_out, amount_in, amount_out}` records in call order |
| `transfers` | Optional `{token, from, to, amount}` records in log order |
| `interactions` | Optional protocol labels in call order |
| `caller` | Account calling the PropAMM; used to match loan transfers |
| `base_token`, `quote_token` | Default WBNB/USDT; set WETH/USDC for Base |
| `reference_price` | Optional independent price in quote per base, aligned to the transaction time |
| `timestamp`, `block_number` | Metadata for reference alignment; not used by the current classifier |

### Decision rule

1. Find opposite-direction swaps on the pair, one on the PropAMM and one on another
   venue. Either venue may come first. The later input must be no greater than the
   earlier output, with 1% tolerance; unspent output is allowed.
2. Evaluate every candidate and report the largest pool disadvantage $d$.
3. Return `True` when $d \ge 30$ bps. A flashloan hint adds context but is not required.

For PropAMM fill price $p$ and reference $R$, both in quote per base:

```math
d = 10^4 \begin{cases}
\dfrac{R-p}{R}, & \text{pool sells base}, \\
\dfrac{p-R}{R}, & \text{pool buys base}.
\end{cases}
```

The observed trade gives **584.2 bps** at $R=624$. The 30 bps threshold is an
illustrative alert setting, separate from the assignment's 5 bps spread; calibrate
it against normal fills and reference error.

**Flashloan hint.** The first and last labels must name the same known lender, with
an intervening call. Alternatively, the first and last transfers must show a loan
to `caller` and repayment of at least that amount to the same counterparty and token.
These are heuristics, not proof of a flashloan; extra outer transfers can hide the bracket.

**Limits.** Without `reference_price`, the other venue's fill is used, so the detector
cannot identify which venue was stale. Amount matching does not prove common ownership
or trace funding through routers; partial matches can join unrelated legs. This is an
alert for two-leg patterns, not a complete detector for multi-hop or cross-transaction arb.

`analyse_trade()` returns the selected pair's prices, disadvantage, gross P&L,
candidate count, and reasons. Gross P&L values leftover base at the reference; it
excludes loan fees and gas and is not the whole transaction's profit.

## 3. Defend

Reject swaps when the quote is expired, its source observation is too old, or its
price deviates from an independent reference. The updater must preserve the source
timestamp when resending an observation.

```text
// state: P (quote per base), source_ts, write_block
// configuration: EXPIRY_BLOCKS and MAX_SOURCE_AGE_S from section 4
// MAX_DEV_BPS = 50 is illustrative; prices share one fixed-point scale

function updatePrice(newP, observed_at):
    require(authorizedUpdater(msg.sender))
    require(newP > 0 && 0 < observed_at <= block.timestamp)
    require(observed_at > source_ts, "replayed observation")
    require(block.timestamp - observed_at <= MAX_SOURCE_AGE_S, "source stale")
    P, source_ts, write_block = newP, observed_at, block.number

function swap(amount_in, direction):
    require(P > 0 && !paused)
    require(block.number - write_block <= EXPIRY_BLOCKS, "quote expired")
    require(block.timestamp - source_ts <= MAX_SOURCE_AGE_S, "source stale")

    // Adapter validates each feed's positive answer and timestamp, then converts
    // to quote per base. Missing, future-dated or stale observations revert.
    ref = referenceAdapter.readValidatedQuotePerBase()
    require(abs(P - ref) * 10000 <= MAX_DEV_BPS * ref, "price deviation")

    return executeSwap(amount_in, direction, P)
```

For BNB/USDT, the adapter can divide BNB/USD by USDT/USD after normalizing decimals;
for ETH/USDC, use ETH/USD and USDC/USD. Validate every constituent feed's age against
its configured limit. Chainlink feeds update on heartbeat or deviation triggers,
so a recently read value is not necessarily a recent observation.
[Reference validation](https://docs.chain.link/data-feeds#check-the-timestamp-of-the-latest-answer).

**Effect.** A reference close to market rejects the observed 6% gap at a 50 bps
limit. This bounds deviation from the reference, not total loss or deviation from
the live market if the reference lags. Reference lag can also halt valid quotes.

**Operation.** The bot checks the same conditions before publishing and alerts on
failure. Source timestamps are trusted assertions by the authorized updater. If the
feed stops, stop publishing and let expiry halt swaps; resume only with fresh,
validated data. Use a separate deployment for tests. Caller or lender blocklists
do not protect against traders using their own funds.

## 4. Threshold: price expiry

Use **6 BSC blocks and 4 Base blocks if refreshing every block**, under the latency
budget below. A slower refresh schedule requires recalculation.

| Symbol | Budget | Unit |
|---|---|---|
| $b$ | Block time: BSC 0.75, Base 2, as given in the assignment | Seconds |
| $T_r$ | Scheduled refresh interval; latest available observation selected at submission | Seconds |
| $T_s$ | Expected WebSocket silence: 2 | Seconds |
| $T_i$ | Inclusion allowance: one block | Seconds |
| $T_m$ | Scheduling and RPC margin: one block | Seconds |
| $N$ | Maximum permitted block age (`EXPIRY_BLOCKS`) | Blocks |
| $A$ | Maximum source age (`MAX_SOURCE_AGE_S`), rounded to whole seconds | Seconds |

Budget the refresh wait, feed silence, inclusion, and margin separately:

```math
N = \left\lceil \frac{T_r + T_s + T_i + T_m}{b} \right\rceil,
\qquad A = \left\lceil Nb \right\rceil.
```

For every-block refresh, $T_r=T_i=T_m=b$:

```math
\begin{aligned}
N_{\mathrm{BSC}} &= \left\lceil \frac{0.75+2+0.75+0.75}{0.75} \right\rceil = 6, \\
N_{\mathrm{Base}} &= \left\lceil \frac{2+2+2+2}{2} \right\rceil = 4.
\end{aligned}
```

| Refresh schedule | BSC: $N$ / $A$ | Base: $N$ / $A$ |
|---|---|---|
| Every block | **6 blocks / 5 s** | **4 blocks / 8 s** |
| Every 13 BSC blocks / 5 Base blocks | **18 blocks / 14 s** | **8 blocks / 16 s** |

The second row illustrates the cadence inferred from the assignment's cost figures:

```math
T_{\mathrm{avg}} = \frac{86400 \times 0.50}{7.2 \times 627}
\approx 9.57\ \mathrm{s}.
```

Here 0.50 is USD per refresh and $7.2\times627$ is USD per day. Rounding the average
up to scheduled blocks gives $T_r=9.75$ s on BSC and $T_r=10$ s on Base. The resulting
expiry is $\lceil(9.75+2+0.75+0.75)/0.75\rceil=18$ and
$\lceil(10+2+2+2)/2\rceil=8$ blocks. The contradictory parenthetical gas estimate in
the assignment is ignored in favor of the stated 0.50 USD per refresh.

These are planning budgets, not measured worst cases. Validate refresh and inclusion
delays before deployment; average spend alone does not establish a safe expiry.
The checks allow age equal to the limit and reject greater ages. Source age remains
independent of block age, so resubmission or slower blocks cannot keep old data alive.

**The 2-second silence is expected data ageing.** It fits the budget but does not
guarantee price accuracy. Longer gaps eventually expire the quote; moves within the
window rely on the reference check, subject to its own lag. The attack's stale duration
is unknown, so expiry alone cannot be claimed to have prevented it.
