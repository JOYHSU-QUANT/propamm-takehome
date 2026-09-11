"""Tests for is_suspicious_trade; run with unittest discovery from the repo root."""

import copy
import unittest

from part1_pricing.pricing import get_quote
from part3_arb_defence.arb_detection import (
    OBSERVED_ATTACK,
    analyse_trade,
    is_suspicious_trade,
)


def _tx(swaps, interactions=None, reference_price=None, **extra):
    tx = {"swaps": swaps, "interactions": interactions or [s["protocol"] for s in swaps],
          "caller": "0xtrader", "timestamp": 0, "block_number": 0}
    if reference_price is not None:
        tx["reference_price"] = reference_price
    tx.update(extra)
    return tx


def _swap(protocol, token_in, token_out, amount_in, amount_out):
    return {"protocol": protocol, "token_in": token_in, "token_out": token_out,
            "amount_in": amount_in, "amount_out": amount_out}


class DetectionTests(unittest.TestCase):
    def test_observed_attack_is_flagged(self):
        a = analyse_trade(OBSERVED_ATTACK)
        self.assertTrue(a.suspicious)
        self.assertTrue(a.round_trip)
        self.assertTrue(a.flashloan)
        self.assertAlmostEqual(a.propamm_price, 10 / 0.01702, places=6)      # ~587.5
        self.assertAlmostEqual(a.other_price, 10.627 / 0.01702, places=6)    # ~624.4
        self.assertAlmostEqual(a.pool_disadvantage_bps, (624 - 10 / 0.01702) / 624 * 1e4, places=6)
        self.assertGreater(a.pool_disadvantage_bps, 500)                      # ~585 bps
        self.assertAlmostEqual(a.trader_gross_profit_quote, 0.627, places=9)

    def test_part1_model_reproduces_the_observed_fill(self):
        # Balanced pool at the stale price 587: a 10 USDT buy fills on the curve
        # at ~587 with negligible slippage, matching the observed 0.01702 WBNB.
        out, eff, _ = get_quote(100, 58_700, 587, 1.02, 5, 10, False)
        self.assertAlmostEqual(out, 0.01702, delta=0.01702 * 1e-3)
        self.assertLess(eff, 624)  # the pool sold below market

    def test_flashloan_is_not_required(self):
        tx = copy.deepcopy(OBSERVED_ATTACK)
        tx["interactions"] = ["PropAMM", "PancakeV2"]
        tx["transfers"] = tx["transfers"][1:-1]
        a = analyse_trade(tx)
        self.assertFalse(a.flashloan)
        self.assertTrue(a.suspicious)

    def test_flashloan_detected_from_transfers_when_labels_missing(self):
        tx = copy.deepcopy(OBSERVED_ATTACK)
        tx["interactions"] = []
        self.assertTrue(analyse_trade(tx).flashloan)

    def test_reverse_order_is_also_flagged(self):
        # Buy cheap on Pancake, sell dear to a PropAMM whose oracle is 6% above market.
        tx = _tx([
            _swap("PancakeV2", "USDT", "WBNB", 10.0, 10 / 624),
            _swap("PropAMM", "WBNB", "USDT", 10 / 624, 10 / 624 * 661),
        ], reference_price=624)
        a = analyse_trade(tx)
        self.assertTrue(a.suspicious)
        self.assertGreater(a.trader_gross_profit_quote, 0)

    def test_single_direction_aggregator_route_is_not_flagged(self):
        # A router splits one buy across PropAMM and Pancake: no reverse leg.
        tx = _tx([
            _swap("PropAMM", "USDT", "WBNB", 300.0, 300 / 627),
            _swap("PancakeV2", "USDT", "WBNB", 200.0, 200 / 627.4),
        ], reference_price=627)
        a = analyse_trade(tx)
        self.assertFalse(a.round_trip)
        self.assertFalse(a.suspicious)

    def test_round_trip_within_threshold_is_not_flagged(self):
        # 8 bps gap: a real arb on a tiny mispricing, below the 30 bps threshold.
        tx = _tx([
            _swap("PropAMM", "USDT", "WBNB", 500.0, 500 / 627.0),
            _swap("PancakeV2", "WBNB", "USDT", 500 / 627.0, 500 / 627.0 * 627.5),
        ], reference_price=627.5)
        a = analyse_trade(tx)
        self.assertTrue(a.round_trip)
        self.assertFalse(a.suspicious)
        self.assertLess(a.pool_disadvantage_bps, 30)
        # The same trade is flagged if the operator tightens the threshold.
        self.assertTrue(is_suspicious_trade(tx, price_gap_bps=5))

    def test_arb_using_us_is_not_flagged_when_reference_is_known(self):
        # Pancake is stale at 587, PropAMM correct at 624. Trader buys on Pancake,
        # sells to PropAMM at bid = P = 624. The pool paid the fair price: no loss.
        swaps = [
            _swap("PancakeV2", "USDT", "WBNB", 10.0, 10 / 587),
            _swap("PropAMM", "WBNB", "USDT", 10 / 587, 10 / 587 * 624),
        ]
        with_ref = analyse_trade(_tx(swaps, reference_price=624))
        self.assertFalse(with_ref.suspicious)
        self.assertAlmostEqual(with_ref.pool_disadvantage_bps, 0, places=6)
        # Without a reference the detector cannot tell which venue was stale and
        # conservatively flags the round trip.
        without_ref = analyse_trade(_tx(swaps))
        self.assertTrue(without_ref.suspicious)

    def test_benign_round_trip_does_not_hide_a_later_attack(self):
        # A fair round trip first, then the attack in the same transaction.
        fair = [
            _swap("PropAMM", "USDT", "WBNB", 100.0, 100 / 624),
            _swap("PancakeV2", "WBNB", "USDT", 100 / 624, 100.05),
        ]
        tx = copy.deepcopy(OBSERVED_ATTACK)
        tx["swaps"] = fair + tx["swaps"]
        a = analyse_trade(tx)
        self.assertTrue(a.suspicious)
        self.assertGreaterEqual(a.pairs_checked, 2)   # cross-pairs under the "funded by" rule are fine
        self.assertGreater(a.pool_disadvantage_bps, 500)   # the worst pair is reported

    def test_flashloan_requires_same_lender_first_and_last(self):
        for inter in (["Aave"], ["Aave", "PropAMM", "Venus"], ["PropAMM", "PancakeV2", "Aave"]):
            tx = copy.deepcopy(OBSERVED_ATTACK)
            tx["interactions"] = inter
            tx["transfers"] = []
            with self.subTest(interactions=inter):
                self.assertFalse(analyse_trade(tx).flashloan)
        tx = copy.deepcopy(OBSERVED_ATTACK)
        tx["interactions"] = ["Aave", "PropAMM", "PancakeV2", "Aave"]
        self.assertTrue(analyse_trade(tx).flashloan)

    def test_chaining_follows_call_order(self):
        # Sell WBNB on Pancake first, then buy it back cheaper from the stale PropAMM,
        # keeping the difference. Funded by own inventory, still an arb against us.
        tx = _tx([
            _swap("PancakeV2", "WBNB", "USDT", 0.01702, 10.627),
            _swap("PropAMM", "USDT", "WBNB", 10.0, 0.01702),
        ], reference_price=624)
        a = analyse_trade(tx)
        self.assertTrue(a.round_trip)
        self.assertTrue(a.suspicious)
        self.assertAlmostEqual(a.trader_gross_profit_quote, 0.627, places=9)
        # Same legs but the trader spent more than the first leg produced: the second
        # leg is not funded by the first, so this is not one round trip.
        tx = _tx([
            _swap("PropAMM", "WBNB", "USDT", 0.01702, 10.0),
            _swap("PancakeV2", "USDT", "WBNB", 10.627, 0.01702),
        ], reference_price=624)
        self.assertFalse(analyse_trade(tx).round_trip)

    def test_unchained_legs_are_not_a_round_trip(self):
        # Reverse direction but unrelated size: not one round trip.
        tx = _tx([
            _swap("PropAMM", "USDT", "WBNB", 10.0, 10 / 587),
            _swap("PancakeV2", "WBNB", "USDT", 1.0, 624.0),
        ], reference_price=624)
        self.assertFalse(analyse_trade(tx).round_trip)

    def test_other_pairs_are_ignored(self):
        tx = _tx([
            _swap("PropAMM", "USDC", "WETH", 1000.0, 0.4),
            _swap("UniswapV3", "WETH", "USDC", 0.4, 1060.0),
        ], reference_price=624)
        a = analyse_trade(tx)
        self.assertFalse(a.suspicious)
        self.assertIn("no PropAMM swap on the base/quote pair", a.reasons[0])
        # Same transaction, evaluated for the Base pair, is flagged.
        tx.update(base_token="WETH", quote_token="USDC", reference_price=2650)
        self.assertTrue(is_suspicious_trade(tx))


if __name__ == "__main__":
    unittest.main()
