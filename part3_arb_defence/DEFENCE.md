# Part 3 - Flashloan Arb Detection & Defence

[Project README](../README.md) / [Detection code](arb_detection.py) / [Tests](test_arb_detection.py) / [Part 1 model](../part1_pricing/MODEL.md)

## Observed transaction

1. Flashloan 10 USDT from Lista DAO
2. PropAMM swap 10 USDT -> 0.01702 WBNB (implied ~587.5 USDT/WBNB)
3. PancakeSwap V2 swap 0.01702 WBNB -> 10.627 USDT (implied ~624.4 USDT/WBNB)
4. Repay flashloan
5. Profit ~0.63 USDT

Root cause: the on-chain oracle price P was ~6% below market during a test session.

## 1. Diagnose

The Part 1 model quotes $\mathrm{bid}=\min(P,y/x)$ and $\mathrm{ask}=\max(P,y/x)$, so the
pool never gives a trader a better price than its oracle $P$. That is a no-arbitrage
guarantee only *relative to $P$*. When $P$ lags the market, the guarantee becomes the
loss: with $P=587$ and the pool near balance, the ask was about 587 while WBNB traded
at 624 on PancakeSwap. Anyone could buy WBNB from the pool at 587 and sell it 6% higher
one call later.

The model reproduces the fill within its assumptions. A 10 USDT buy against a pool
balanced at $P=587$, with $\alpha=1.02$ and a 5 bps fee, returns 0.01703 WBNB; the
observed 0.01702 is consistent with that, the small difference coming from fee, alpha or
reserves we do not observe. Selling 0.01702 WBNB at 624.4 yields 10.627 USDT. The pool
gave up 0.01702 WBNB worth 10.63 at market for 10.00 USDT, so the 0.63 USDT profit is a
transfer from the pool. The hedge side realises that same loss: the delta monitor buys
back 0.01702 WBNB on Binance at 624, plus execution cost.

The enabling condition is that the swap path trusted $P$ unconditionally. Nothing on
chain checked how old the price was or how far it sat from an independent reference.
The flashloan is incidental: it made the trade capital-free and atomic, but the profit
existed for anyone holding 10 USDT. It scales with size until the curve's slippage
closes the gap, so the exposure is bounded by inventory and curve depth, not by the
attacker's capital. 10 USDT reads as a probe of that bound.

(263 words)

## 2. Detect

### tx_data contract

`is_suspicious_trade(tx_data)` takes one transaction, already decoded:

| Field | Type | Purpose |
|---|---|---|
| `swaps` | list of `{protocol, token_in, token_out, amount_in, amount_out}` in call order | Round-trip structure and implied prices |
| `transfers` | list of `{token, from, to, amount}` in log order | Flashloan detection when labels are missing |
| `interactions` | list of protocol labels in call order | Flashloan bracket, venue identification |
| `caller` | address | Ties transfers to the initiating account |
| `timestamp`, `block_number` | int | Correlate with the on-chain price update history |
| `base_token`, `quote_token` | str, default WBNB/USDT | Which pair to evaluate |
| `reference_price` | float, optional | Fair price at that block (e.g. Binance mid); see below |

### Heuristics, in order of weight

1. **Round trip through the PropAMM.** One PropAMM swap on the pair, plus a reverse
   swap on another venue *later in the same transaction* that is funded by the first
   leg's output: same token, and it spends at most what the first leg produced (1%
   tolerance). Either venue may come first: buy from us and sell elsewhere, or buy
   elsewhere and sell to us. Every chained pair in the transaction is evaluated and the
   one worst for the pool is reported, so a benign round trip earlier in the same
   transaction cannot hide a later attack.
2. **The PropAMM leg was bad for the pool.** Compute the PropAMM fill price and compare
   with a reference. If the pool sold base below reference, or bought base above it,
   by more than `price_gap_bps` (default 30 bps, well above the 5 bps spread and any
   normal slippage), the trade is flagged. The observed attack scores 584 bps.
3. **Flashloan bracket.** The first and last interaction are the *same* known lender
   with at least one call in between, or the transfers show the same token going
   lender -> caller first and caller -> lender last with at least the borrowed amount
   returned. This raises severity and is reported, but it is not required: heuristic 2
   is the loss, heuristic 3 is only how it was funded.

Conditions 1 and 2 together return `True`. `analyse_trade()` exposes the full
breakdown: prices, disadvantage in bps, the trader's **gross** profit (before flashloan
fee and gas), number of pairs checked, and reasons.

### The reference price matters

Without a reference, the detector uses the other venue's fill as the reference. That is
conservative: a round trip where *Pancake* was stale and the trader sold to us at our
correct bid $= P$ would also be flagged, even though the pool paid a fair price and lost
nothing. With `reference_price` supplied (the operator has Binance mid at every block
anyway), the detector distinguishes "arb against us" from "arb using us" and only flags
the former. The tests cover both readings.

### What it deliberately does not do

- It does not require the caller to be a contract, and it does not care whether the
  trader used a flashloan. Both are trivially avoided by a funded attacker.
- It does not flag single-direction routes that touch both venues (aggregator splits).
- It does not flag round trips inside the threshold. Those are ordinary arbitrage on
  bps-level mispricings and are the cost of quoting; tightening `price_gap_bps` trades
  false negatives for false positives.

## 3. Defend

Two on-chain checks in the swap path, plus one operational rule. Pseudocode:

```text
// state written by the refresh bot in one call
P                 : oracle price, quote per base
P_source_ts       : timestamp of the Binance observation the price came from
P_block           : block in which it was written
EXPIRY_BLOCKS     : from section 4
MAX_SOURCE_AGE_S  : e.g. 5 s
MAX_DEV_BPS       : e.g. 50

function updatePrice(newP, source_ts):
    require(source_ts > P_source_ts, "stale observation")   // re-sending an old price cannot extend expiry
    P, P_source_ts, P_block = newP, source_ts, block.number

function swap(amount_in, direction):
    // (a) freshness, on both clocks
    require(block.number - P_block <= EXPIRY_BLOCKS, "price expired")
    require(block.timestamp - P_source_ts <= MAX_SOURCE_AGE_S, "source stale")

    // (b) deviation against an independent on-chain reference
    (ref, ref_updated_at) = referenceOracle.read()     // Chainlink BNB/USD or a DEX TWAP
    require(ref > 0 && block.timestamp - ref_updated_at <= REF_MAX_AGE_S, "reference stale")
    ref = toQuoteUnits(ref)                            // USD -> USDT via a stablecoin feed, or use a USDT-quoted TWAP
    require(abs(P - ref) * 1e4 <= MAX_DEV_BPS * ref, "oracle deviation")   // integer bps, no floats

    // (c) optional: make staleness cost the trader, not the pool
    staleness_s = block.timestamp - P_source_ts
    fee_bps = BASE_FEE_BPS + K * SIGMA * sqrt(staleness_s)

    (amount_out, price, fee) = get_quote(x, y, P, alpha, fee_bps, amount_in, direction)
    ...
```

**Why these.** Check (a) bounds *how long* a price can be quoted, measured from the
source observation and not only from the write, so the bot cannot keep an old price
alive by re-sending it. Check (b) bounds *how far* $P$ can sit from an independent
reference. The guarantee is conditional: if the reference is fresh and within its own
error of the market, a 6% deviation reverts the swap, and the residual exposure is at
most `MAX_DEV_BPS` plus the reference's own error for at most the expiry window. If the
reference itself lags, the bound is only as good as the reference. Check (c) reuses the
$\sigma\sqrt{T}$ adverse-selection model from Part 2: the longer the price has been
sitting, the more the pool charges for it.

**Implementation notes.** The reference oracle needs the same care as $P$: check its
`updatedAt`, reject non-positive answers, and convert units (a BNB/USD feed is not a
BNB/USDT price; either pair it with a USDT/USD feed or use a USDT-quoted DEX TWAP).
Keep the deviation arithmetic in integer basis points.

**Operational rule.** The refresh bot should apply the same deviation check before
pushing: if its Binance-derived price disagrees with the reference by more than the
bound, it should halt and alert rather than publish. A test session should run against
a separate deployment or behind an allowlist, so a stale test price cannot be traded
by the public.

**What not to do.** Blocking contract callers (`tx.origin == msg.sender`) or blocking
known flashloan lenders does not help. It breaks aggregators and routers that bring
legitimate flow, and a funded attacker gets the same 6% with an EOA and two
transactions. The vulnerability is the price, not the caller.

## 4. Threshold: on-chain price expiry

**What the expiry must satisfy.** It must be longer than the worst-case gap between
two *normal* refreshes, or the pool halts every minute during the 2-second WebSocket
silence; and as short as possible beyond that, because every block of slack is a
block in which a wrong price can be traded.

$$
\text{expiry\_blocks} = \left\lceil \frac{T_{\text{refresh}} + T_{\text{silence}} + T_{\text{inclusion}} + T_{\text{margin}}}{T_{\text{block}}} \right\rceil
$$

- $T_{\text{refresh}}$: the bot's cadence. The on-chain price can already be up to one
  refresh interval old when the silence starts, so this term is always present.
- $T_{\text{silence}} = 2$ s, the known Binance WS gap.
- $T_{\text{inclusion}}$: one block for the refresh transaction to land.
- $T_{\text{margin}}$: one block, for jitter in the bot and the RPC.

**Case 1: the bot refreshes every block** ($T_{\text{refresh}}$ = 1 block).

| Chain | Block time | Refresh | Silence | Inclusion | Margin | Sum | Expiry |
|---|---|---|---|---|---|---|---|
| BSC | 0.75 s | 0.75 | 2 | 0.75 | 0.75 | 4.25 s | ceil(5.67) = **6 blocks (4.5 s)** |
| Base | 2 s | 2 | 2 | 2 | 2 | 8 s | **4 blocks (8 s)** |

**Case 2: the current cadence.** The assignment says the system spends 7.2 BNB/day
per chain at $0.50 per refresh. At $627/BNB that is $4,514/day, or about 9,030
refreshes, one every 9.6 s on average: ~13 BSC blocks, ~5 Base blocks. This is an
*average* inferred from spend, not a worst case; the expiry must be sized from the
observed tail of the refresh gap (e.g. the p99), which the assignment does not give.
If the tail is close to the average:

| Chain | Refresh | Silence | Inclusion | Margin | Sum | Expiry |
|---|---|---|---|---|---|---|
| BSC | 13 | 3 | 1 | 1 | 18 blocks | round up to **20 blocks (15 s)** |
| Base | 5 | 1 | 1 | 1 | 8 blocks | **8 blocks (16 s)** |

If the tail is materially longer than the average, the expiry grows with it, and the
right fix is to tighten the cadence rather than loosen the expiry.

**Is 15 s acceptable?** Price the staleness with the Part 2 model. BNB at ~70%
annualised volatility gives $\sigma \approx 1.25$ bps per $\sqrt{\text{s}}$, so the
*typical* move over a window is $\sigma\sqrt{T}$ (a scale, not a bound):

| Window | Typical move |
|---|---|
| 2 s (WS silence) | 1.8 bps |
| 4.5 s (BSC, case 1) | 2.6 bps |
| 8 s (Base, case 1) | 3.5 bps |
| 15 s (BSC, case 2) | 4.8 bps |
| 60 s | 9.7 bps |

At 15 s the typical drift is about one 5 bps spread, which is the most the pool can
tolerate before it is systematically giving away the spread. This is why the expiry
should follow the refresh cadence rather than be fixed: if Part 2's optimisation moves
the bot toward every-block refreshes, tighten to 6 and 4 blocks; if it moves toward
20 s, the pool needs the deviation bound from section 3 to stay safe.

**Two consequences.**

- The 2-second silence is expected data ageing, not a failure. Its typical drift is
  inside the spread, and the expiry above is sized so that a normal silence never trips
  it. It does not by itself justify halting quotes, but it is a scale, not a guarantee:
  a large move inside the silence is caught by the deviation bound, not by the expiry.
  What the silence must not do is silently extend: the bot should treat a gap longer
  than ~3 s as a feed failure and stop pushing, letting the on-chain expiry do its job.
- The assignment does not say how long the attack price had been stale. Any expiry in
  the tables bounds that to seconds; the deviation bound in section 3 catches a 6% gap
  regardless of age, provided the reference is fresh.

## Assumptions

- **Fill prices are taken from swap amounts.** `amount_in / amount_out` is the
  effective price including that venue's fee; the 30 bps threshold is set with that in
  mind.
- **Reference price is external.** When supplied it is Binance mid (or Chainlink) at
  the block; when absent the detector conservatively uses the other venue.
- **Refresh cadence from the gas figure.** The 9.6 s cadence is an average inferred
  from 7.2 BNB/day at $0.50 per refresh and $627/BNB. The assignment's wording "approx
  1 BNB refresh at $627" is ambiguous; $0.50 per refresh is used as stated.
- **Volatility.** 70% annualised for BNB is an order-of-magnitude assumption for
  sizing the expiry; the conclusions hold for 50 to 100%.
- **Reproduction parameters.** The tests use reserves balanced at $P=587$,
  $\alpha=1.02$ and a 5 bps fee; the assignment gives none of these for the attack, and
  other plausible values move the fill by a fraction of a percent.
