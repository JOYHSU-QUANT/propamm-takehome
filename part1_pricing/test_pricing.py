"""Regression and model-property tests; run with unittest discovery."""

import math
import unittest

from part1_pricing.pricing import (
    InsufficientLiquidity,
    get_bid_ask,
    get_quote,
    get_quote_detailed,
)


class PricingTests(unittest.TestCase):
    def assertClose(self, actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        self.assertTrue(
            math.isclose(actual, expected, rel_tol=rel_tol, abs_tol=abs_tol),
            f"{actual} != {expected}",
        )

    def test_official_cases(self):
        cases = [
            (100, 62700, 1.02, 0.791261985291836, 631.9019607843137, 627, 627),
            (80, 75000, 1.02, 0.5298701298701298, 943.6274509803923, 627, 937.5),
            (120, 50000, 1.02, 0.7974481658692185, 627, 50000 / 120, 627),
            (100, 62700, 1.05, 0.7914374010703249, 631.7619047619047, 627, 627),
        ]
        for x, y, alpha, expected_out, expected_price, bid, ask in cases:
            with self.subTest(x=x, y=y, alpha=alpha):
                out, price, fee = get_quote(x, y, 627, alpha, 0, 500, False)
                self.assertClose(out, expected_out)
                self.assertClose(price, expected_price)
                self.assertEqual(fee, 0)
                self.assertEqual(get_bid_ask(x, y, 627, alpha), (bid, ask))

    def test_invalid_numeric_inputs(self):
        base = dict(reserve_x=100, reserve_y=62700, price_P=627, alpha=1.02,
                    fee_bps=0, amount_in=500, swap_x_to_y=False)
        invalid = {
            "reserve_x": [0, -1], "reserve_y": [0, -1],
            "price_P": [0, -1], "alpha": [0.99],
            "amount_in": [0, -1], "fee_bps": [-1, 10000, 10001],
        }
        for name, values in invalid.items():
            for value in values + [math.nan, math.inf, -math.inf]:
                for direction in (False, True):
                    with self.subTest(name=name, value=value, direction=direction):
                        params = dict(base, **{name: value, "swap_x_to_y": direction})
                        with self.assertRaises(ValueError):
                            get_quote(**params)
                        if name in ("reserve_x", "reserve_y", "price_P", "alpha"):
                            with self.assertRaises(ValueError):
                                get_bid_ask(*(params[k] for k in (
                                    "reserve_x", "reserve_y", "price_P", "alpha")))

    def test_invalid_direction(self):
        for direction in ("False", 0, 1, None):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                get_quote(100, 62700, 627, 1.02, 0, 500, direction)

    def test_fee_accounting_and_price_units(self):
        for direction, x, y, gross in [(False, 120, 50000, 500), (True, 80, 75000, 1)]:
            with self.subTest(direction=direction):
                out, price, fee, bd = get_quote_detailed(x, y, 627, 1.02, 5, gross, direction)
                net = gross * 0.9995
                self.assertClose(fee, gross * 0.0005)
                self.assertClose(bd.stable_in, net)
                self.assertEqual(bd.curve_in, 0)
                self.assertClose(out, net * 627 if direction else net / 627)
                self.assertClose(price, 627 * 0.9995 if direction else 627 / 0.9995)
                final_x = x + net if direction else x - out
                final_y = y - out if direction else y + net
                self.assertClose(bd.reserve_ratio_after, final_y / final_x)

    def test_curve_invariant_and_marginal_bounds(self):
        # Pure curve and stable -> curve in both directions, including alpha=1.
        for direction, x, y, gross in [
            (False, 100, 62700, 500), (True, 100, 62700, 1),
            (False, 120, 50000, 20000), (True, 80, 75000, 30),
        ]:
            for alpha in (1, 1.02, 1.05):
                for fee_bps in (0, 5):
                    with self.subTest(direction=direction, x=x, alpha=alpha, fee=fee_bps):
                        out, price, fee, bd = get_quote_detailed(
                            x, y, 627, alpha, fee_bps, gross, direction)
                        self.assertGreater(bd.curve_in, 0)
                        self.assertClose(bd.stable_in + bd.curve_in + fee, gross)
                        self.assertClose(bd.stable_out + bd.curve_out, out)
                        sx = x + bd.stable_in if direction else x - bd.stable_out
                        sy = y - bd.stable_out if direction else y + bd.stable_in
                        vx, vy = sx * (alpha - 1), sy * (alpha - 1)
                        ex = sx + bd.curve_in if direction else sx - bd.curve_out
                        ey = sy - bd.curve_out if direction else sy + bd.curve_in
                        self.assertClose((ex + vx) * (ey + vy), (sx + vx) * (sy + vy))
                        self.assertClose(bd.reserve_ratio_after, ey / ex)
                        bid, ask = get_bid_ask(x, y, 627, alpha)
                        if direction:
                            self.assertLessEqual(price, bid)
                        else:
                            self.assertGreaterEqual(price, ask)

    def test_stable_boundary_continuity_and_net_capacity(self):
        # Both chosen pools have a stable capacity of 10 input tokens at P=2.
        for direction, x, y in [(False, 60, 100), (True, 40, 120)]:
            for fee_bps in (0, 5):
                with self.subTest(direction=direction, fee=fee_bps):
                    gross = 10 / (1 - fee_bps / 10000)
                    h = 1e-5
                    lo = get_quote_detailed(x, y, 2, 1.02, fee_bps, gross - h, direction)
                    at = get_quote_detailed(x, y, 2, 1.02, fee_bps, gross, direction)
                    hi = get_quote_detailed(x, y, 2, 1.02, fee_bps, gross + h, direction)
                    self.assertEqual(lo[3].curve_in, 0)
                    self.assertClose(at[3].stable_in, 10)
                    self.assertClose(at[3].curve_in, 0)
                    self.assertGreater(hi[3].curve_in, 0)
                    self.assertClose(at[3].reserve_ratio_after, 2)
                    slope = (2 if direction else 0.5) * (1 - fee_bps / 10000)
                    self.assertClose((at[0] - lo[0]) / h, slope, rel_tol=1e-6)
                    self.assertClose((hi[0] - at[0]) / h, slope, rel_tol=1e-6)

    def test_real_reserve_exhaustion(self):
        # Effective reserves are 200/200; an input of 200 exhausts 100 real tokens.
        for direction in (False, True):
            with self.subTest(direction=direction):
                out, _, _ = get_quote(100, 100, 1, 2, 0, 199, direction)
                self.assertLess(out, 100)
                for amount in (200, 201):
                    with self.assertRaises(InsufficientLiquidity):
                        get_quote(100, 100, 1, 2, 0, amount, direction)

    def test_reserve_ratio_is_distinct_from_fixed_curve_endpoint(self):
        out, _, _, bd = get_quote_detailed(100, 62700, 627, 1.02, 0, 500, False)
        self.assertClose(bd.reserve_ratio_after, 63200 / (100 - out))
        curve_endpoint = (62700 * 1.02 + 500) / (100 * 1.02 - out)
        self.assertFalse(math.isclose(bd.reserve_ratio_after, curve_endpoint, rel_tol=1e-6))

    def test_splitting_never_improves_output(self):
        # A9: same fee, fixed P and alpha, same direction, reserves updated by
        # the net (post-fee) input after each fill. Splitting never increases
        # output; it is strictly lower only when alpha > 1 and at least two
        # pieces each carry positive curve input. Checked at 0, 5 and 30 bps.
        FEES = (0, 5, 30)

        def split(x, y, alpha, fee_bps, pieces, direction):
            out_sum = 0.0
            for amt in pieces:
                out, _, fee = get_quote(x, y, 627, alpha, fee_bps, amt, direction)
                out_sum += out
                net = amt - fee
                x, y = (x + net, y - out) if direction else (x - out, y + net)
            return out_sum

        def single(x, y, alpha, fee_bps, total, direction):
            return get_quote(x, y, 627, alpha, fee_bps, total, direction)[0]

        # Zero-fee reference number from A9.
        self.assertClose(
            single(100, 62700, 1.02, 0, 500, False) - split(100, 62700, 1.02, 0, [250, 250], False),
            6.13772e-5, rel_tol=1e-4)
        self.assertClose(split(120, 50000, 1.02, 0, [15000, 5000], False), 30.66907534168, rel_tol=1e-10)

        for fee_bps in FEES:
            # Pure curve phase, alpha > 1, both directions: strictly worse.
            for direction, x, y, total in [(False, 100, 62700, 500), (True, 100, 62700, 1)]:
                for alpha in (1.02, 1.05):
                    with self.subTest(case="pure-curve", fee=fee_bps, direction=direction, alpha=alpha):
                        self.assertLess(split(x, y, alpha, fee_bps, [total / 2] * 2, direction),
                                        single(x, y, alpha, fee_bps, total, direction))

            # Stable -> curve crossing, both directions. Case E reserves for Y -> X
            # (net stable capacity 12,620 USDT); Case B reserves for X -> Y
            # (net stable capacity (75000 - 80*627) / (2*627) WBNB). The boundary
            # in gross terms is net capacity / (1 - fee).
            net_cap_x = (75000 - 80 * 627) / (2 * 627)
            crossing = [
                (False, 120, 50000, 20000, 12620),
                (True, 80, 75000, 60, net_cap_x),
            ]
            for direction, x, y, total, net_cap in crossing:
                cap = net_cap / (1 - fee_bps / 10000)
                base = single(x, y, 1.02, fee_bps, total, direction)
                # Split at or before the boundary: curve portion stays in one piece -> identical.
                for pieces in ([cap, total - cap], [cap / 2, total - cap / 2]):
                    with self.subTest(case="cross-identical", fee=fee_bps, direction=direction, pieces=pieces):
                        self.assertClose(split(x, y, 1.02, fee_bps, pieces, direction), base, rel_tol=1e-12)
                # Split after the boundary: two pieces each with curve input -> strictly worse.
                after = cap + (total - cap) / 2
                with self.subTest(case="cross-worse", fee=fee_bps, direction=direction):
                    self.assertLess(split(x, y, 1.02, fee_bps, [after, total - after], direction), base)

            # alpha = 1 (plain CPMM, path independent), both directions: identical.
            for direction, x, y, total in [(False, 100, 62700, 500), (True, 100, 62700, 1)]:
                with self.subTest(case="alpha1", fee=fee_bps, direction=direction):
                    self.assertClose(split(x, y, 1, fee_bps, [total / 2] * 2, direction),
                                     single(x, y, 1, fee_bps, total, direction), rel_tol=1e-12)

            # Entirely stable phase (flat price P), both directions: identical.
            for direction, x, y, total in [(False, 120, 50000, 500), (True, 80, 75000, 1)]:
                with self.subTest(case="stable-only", fee=fee_bps, direction=direction):
                    self.assertClose(split(x, y, 1.02, fee_bps, [total / 2] * 2, direction),
                                     single(x, y, 1.02, fee_bps, total, direction), rel_tol=1e-12)

    def test_tiny_trades_approach_bid_ask(self):
        for x, y in [(100, 62700), (80, 75000), (120, 50000)]:
            with self.subTest(x=x):
                bid, ask = get_bid_ask(x, y, 627, 1.02)
                self.assertClose(get_quote(x, y, 627, 1.02, 0, 1e-7, True)[1], bid, rel_tol=1e-8)
                self.assertClose(get_quote(x, y, 627, 1.02, 0, 1e-5, False)[1], ask, rel_tol=1e-8)


if __name__ == "__main__":
    unittest.main()
