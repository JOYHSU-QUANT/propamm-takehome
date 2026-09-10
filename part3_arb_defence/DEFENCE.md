# Part 3 - Flashloan Arb Detection & Defence

[Project README](../README.md) / [Detection code](arb_detection.py) / [Part 1 model](../part1_pricing/MODEL.md)

## Observed transaction

1. Flashloan 10 USDT from Lista DAO
2. PropAMM swap 10 USDT -> 0.01702 WBNB (implied ~587 USDT/WBNB)
3. PancakeSwap V2 swap 0.01702 WBNB -> 10.627 USDT (market ~624 USDT/WBNB)
4. Repay flashloan
5. Profit ~0.63 USDT

Root cause: stale oracle price, ~6% below market.

## 1. Diagnose

_TODO (max 300 words)_

## 2. Detect

### tx_data fields

_TODO_

### Heuristics

_TODO_

## 3. Defend

_TODO (pseudocode or plain English)_

## 4. Threshold: on-chain price expiry

_TODO: expiry in blocks for BSC (0.75 s) and Base (2 s), with working_

## Assumptions

_TODO_
