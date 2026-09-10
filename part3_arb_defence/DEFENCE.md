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
loss: with $P=587$ and the pool near balance, the ask was 587 while WBNB traded at 624
on PancakeSwap. Anyone could buy WBNB from the pool at 587 and sell it 6% higher one
call later.

The numbers reproduce exactly from the model. A 10 USDT buy against a balanced pool at
$P=587$ returns 0.01703 WBNB on the curve with negligible slippage; the observed
0.01702 differs only by the fee. Selling 0.01702 WBNB at 624.4 yields 10.627 USDT.
The pool sold 0.01702 WBNB for 10.00 USDT that was worth 10.63 at market, so the
0.63 USDT profit is a direct transfer from the pool. The hedge side then compounds it:
the delta monitor sees the pool short 0.01702 WBNB and buys it back on Binance at 624,
realising the loss in the hedge book.

The enabling condition is that the swap path trusted $P$ unconditionally. Nothing on
chain checked how old the price was or how far it sat from an independent reference.
The flashloan is incidental: it made the trade capital-free and atomic, but the profit
existed for anyone holding 10 USDT, and it scales linearly with size until the curve's
slippage closes the 6% gap. At 10 USDT this was a probe. At pool depth it would have
been the whole inventory, repriced at a 6% discount, block after block, until the next
refresh.

(266 words)

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
   swap on another venue in the same transaction whose input matches the PropAMM
   output within 1%. Either order counts: buy from us and sell elsewhere, or buy
   elsewhere and sell to us.
2. **The PropAMM leg was bad for the pool.** Compute the PropAMM fill price and compare
   with a reference. If the pool sold base below reference, or bought base above it,
   by more than `price_gap_bps` (default 30 bps, well above the 5 bps spread and any
   normal slippage), the trade is flagged. The observed attack scores 584 bps.
3. **Flashloan bracket.** The first and last interaction are a known lender, or the
   transfers show the same token going lender -> caller first and caller -> lender
   last with at least the borrowed amount returned. This raises severity and is
   reported, but it is not required: heuristic 2 is the loss, heuristic 3 is only
   how it was funded.

Conditions 1 and 2 together return `True`. `analyse_trade()` exposes the full
breakdown (prices, disadvantage in bps, trader profit, reasons) for alerting.

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
// state set by the refresh bot
P               : oracle price
P_block         : block number of the last refresh
EXPIRY_BLOCKS   : from section 4
MAX_DEV_BPS     : e.g. 50

function swap(amount_in, direction):
    // (a) freshness: never quote on an expired price, never fall back to the last one
    require(block.number - P_block <= EXPIRY_BLOCKS, "price expired")

    // (b) deviation: compare P with an independent on-chain reference
    ref = referenceOracle.read()        // Chainlink BNB/USD, or a PancakeSwap TWAP
    dev_bps = abs(P - ref) / ref * 1e4
    require(dev_bps <= MAX_DEV_BPS, "oracle deviation")

    // (c) optional: make staleness cost the trader, not the pool
    staleness_s = (block.number - P_block) * BLOCK_TIME
    fee_bps = BASE_FEE_BPS + K * SIGMA * sqrt(staleness_s)

    (amount_out, price, fee) = get_quote(x, y, P, alpha, fee_bps, amount_in, direction)
    ...
```

**Why these.** Check (a) bounds *how long* a wrong price can be quoted; check (b)
bounds *how wrong* it can be. Together they cap the extractable value at
`MAX_DEV_BPS` for at most `EXPIRY_BLOCKS`. With `MAX_DEV_BPS = 50` the attack's 6%
edge becomes at most 0.5% minus fees, roughly a 14x reduction; with the expiry from
section 4 the window is seconds, not a test session. Check (c) reuses the
$\sigma\sqrt{T}$ adverse-selection model from Part 2: the longer the price has been
sitting, the more the pool charges for it.

**Operational rule.** The refresh bot should apply the same deviation check before
pushing: if its Binance-derived price disagrees with Chainlink or the DEX TWAP by more
than the bound, it should halt and alert rather than publish. A test session should
run against a separate deployment or behind an allowlist, so a stale test price cannot
be traded by the public.

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

- $T_{\text{silence}} = 2$ s, the known Binance WS gap.
- $T_{\text{inclusion}}$: one block for the refresh transaction to land.
- $T_{\text{margin}}$: one block, for jitter in the bot and the RPC.
- $T_{\text{refresh}}$: the bot's own cadence. Two cases below.

**Case 1: the bot refreshes every block.** Only the silence and latency matter.

| Chain | Block time | Silence | Inclusion | Margin | Expiry |
|---|---|---|---|---|---|
| BSC | 0.75 s | 3 blocks (2 / 0.75 = 2.67) | 1 | 1 | **5 blocks (3.75 s)** |
| Base | 2 s | 1 block | 1 | 1 | **3 blocks (6 s)** |

**Case 2: the current cadence.** The assignment says the system spends 7.2 BNB/day
per chain at $0.50 per refresh. At $627/BNB that is $4,514/day, or about 9,030
refreshes, one every 9.6 s. On BSC that is ~13 blocks between refreshes; on Base ~5.

| Chain | Refresh | Silence | Inclusion | Margin | Expiry |
|---|---|---|---|---|---|
| BSC | 13 | 3 | 1 | 1 | 18, round to **20 blocks (15 s)** |
| Base | 5 | 1 | 1 | 1 | **8 blocks (16 s)** |

**Is 15 s acceptable?** Price the staleness with the Part 2 model. BNB at ~70%
annualised volatility moves $\sigma\sqrt{T}$ with $\sigma \approx 1.25$ bps per
$\sqrt{\text{s}}$:

| Window | Typical move |
|---|---|
| 2 s (WS silence) | 1.8 bps |
| 3.75 s (BSC, case 1) | 2.4 bps |
| 15 s (BSC, case 2) | 4.8 bps |
| 60 s | 9.7 bps |

At 15 s the typical drift is about one 5 bps spread, which is the most the pool can
tolerate before it is systematically giving away the spread. This is why the expiry
should follow the refresh cadence rather than be fixed: if Part 2's optimisation moves
the bot toward every-block refreshes, tighten to 5 and 3 blocks; if it moves toward
20 s, the pool needs the deviation bound from section 3 to stay safe.

**Two consequences.**

- The 2-second silence is not a staleness *event*. A 1.8 bps drift is inside the
  spread, and the expiry above is sized so that a normal silence never trips it. What
  the silence must not do is silently extend: the bot should treat a gap longer than
  ~3 s as a feed failure and stop pushing, letting the on-chain expiry do its job.
- The observed attack was minutes stale, not seconds. Any expiry in this table would
  have reverted the swap. The deviation bound in section 3 covers the remaining case:
  a fast market move inside an otherwise valid expiry window.

## Assumptions

- **Fill prices are taken from swap amounts.** `amount_in / amount_out` is the
  effective price including that venue's fee; the 30 bps threshold is set with that in
  mind.
- **Reference price is external.** When supplied it is Binance mid (or Chainlink) at
  the block; when absent the detector conservatively uses the other venue.
- **Refresh cadence from the gas figure.** The 9.6 s cadence is inferred from 7.2
  BNB/day at $0.50 per refresh and $627/BNB. The assignment's wording "approx 1 BNB
  refresh at $627" is ambiguous; $0.50 per refresh is used as stated.
- **Volatility.** 70% annualised for BNB is an order-of-magnitude assumption for
  sizing the expiry; the conclusions hold for 50 to 100%.
- **Balanced pool at the time of the attack.** The reproduction in the tests uses
  reserves balanced at $P=587$; an imbalanced pool changes the ask slightly but not
  the conclusion.
