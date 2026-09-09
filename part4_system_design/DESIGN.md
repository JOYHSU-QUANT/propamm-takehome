# Part 4 — System Design: Failure Handling Architecture

## 1. Failure mode matrix

| Failure | What happens | Automated response |
|---|---|---|
| Binance API returns 429 during hedge execution | | |
| BSC RPC primary node goes down mid-refresh | | |
| Operator bot wallet runs out of BNB gas | | |
| On-chain price expiry fires but feed has not recovered | | |
| Delta exceeds $2,000 unhedged due to missed hedge fills | | |
| Safe signers are unreachable for 24 hours | | |

## 2. Kill switch design

_TODO_

## 3. Gas wallet auto top-up

```
# pseudocode
```

## Assumptions

_TODO_
