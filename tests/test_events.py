import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import config, events as ev
from tests.helpers import make_series

EV, LV = config.DEFAULTS["event"], config.DEFAULTS["levelling"]


class EventSession(unittest.TestCase):
    dates = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]

    def test_intraday_news_maps_to_same_session(self):
        self.assertEqual(ev.event_session("2026-09-22T05:00:00+00:00", self.dates), "2026-09-22")   # 10:30 IST

    def test_after_close_news_maps_to_next_session(self):
        self.assertEqual(ev.event_session("2026-09-22T10:30:00+00:00", self.dates), "2026-09-23")   # 16:00 IST

    def test_weekend_news_maps_to_monday(self):
        self.assertEqual(ev.event_session("2026-09-19T06:00:00+00:00", self.dates), "2026-09-21")

    def test_future_session_is_unresolved(self):
        self.assertIsNone(ev.event_session("2026-09-24T12:00:00+00:00", self.dates))


class Dip(unittest.TestCase):
    def test_large_abnormal_drop_is_confirmed(self):
        stock, bench = make_series(shocks={300: -0.09})
        res = ev.assess_dip(stock, bench, stock[300]["date"], EV)
        self.assertEqual(res["status"], "dip_confirmed")
        self.assertLess(res["abnormal_return"], -0.06)
        self.assertLess(res["z_score"], -3)
        self.assertTrue(res["high_volume"])

    def test_normal_day_is_not_a_dip(self):
        stock, bench = make_series()
        res = ev.assess_dip(stock, bench, stock[300]["date"], EV)
        self.assertEqual(res["status"], "no_dip")

    def test_market_wide_fall_is_not_abnormal(self):
        # Stock falls with the market (beta ~1.1): raw drop is large, abnormal is not.
        stock, bench = make_series(beta=1.0, noise=0.004)
        for i in range(300, len(stock)):
            for b, f in ((bench[i], 0.92), (stock[i], 0.92)):
                for k in ("open", "high", "low", "close", "adjclose"):
                    b[k] *= f
        res = ev.assess_dip(stock, bench, stock[300]["date"], EV)
        self.assertLess(res["raw_return"], -0.04)
        self.assertNotEqual(res["status"], "dip_confirmed")

    def test_partial_window_is_provisional(self):
        stock, bench = make_series(n=301)
        res = ev.assess_dip(stock, bench, stock[300]["date"], EV)
        self.assertEqual(res["sessions_evaluated"], 1)
        self.assertEqual(res["status"], "provisional")

    def test_estimation_needs_history(self):
        stock, bench = make_series(shocks={40: -0.2})
        self.assertEqual(ev.assess_dip(stock, bench, stock[40]["date"], EV)["status"], "insufficient_data")

    def test_estimation_window_excludes_event(self):
        stock, bench = make_series(shocks={300: -0.09})
        pairs = ev.align(stock, bench)
        mm_with = ev.market_model(pairs, 300, EV)
        clean, _ = make_series()
        mm_clean = ev.market_model(ev.align(clean, bench), 300, EV)
        self.assertAlmostEqual(mm_with["sigma"], mm_clean["sigma"], places=12)


class Levelling(unittest.TestCase):
    def test_repeated_low_restarts_stability_clock(self):
        stock, bench = make_series(n=330, shocks={300: -0.09}, calm_after=302, seed=11)
        for i in (300, 329):
            stock[i]['low'] = stock[i]['close']/stock[i]['adjclose']
        result = ev.assess_levelling(stock, bench, stock[300]['date'], EV, LV)
        self.assertEqual(result['trough_date'], stock[-1]['date'])
        self.assertFalse(result['checks']['base_held'])

    def test_not_levelled_too_soon(self):
        stock, bench = make_series(n=305, shocks={300: -0.09})
        res = ev.assess_levelling(stock, bench, stock[300]["date"], EV, LV)
        self.assertFalse(res["levelled"])
        self.assertIn("need", res["reason"])

    def test_levels_after_calm_period(self):
        stock, bench = make_series(n=330, shocks={300: -0.09}, calm_after=302, seed=11)
        res = ev.assess_levelling(stock, bench, stock[300]["date"], EV, LV)
        self.assertTrue(res["levelled"], res)
        self.assertIn("recovery_vs_pre_event", res)

    def test_continuing_slide_is_not_levelled(self):
        shocks = {300: -0.09, **{i: -0.02 for i in range(302, 330)}}
        stock, bench = make_series(n=330, shocks=shocks)
        res = ev.assess_levelling(stock, bench, stock[300]["date"], EV, LV)
        self.assertFalse(res["levelled"])
        self.assertFalse(res["checks"]["no_drift"] and res["checks"]["base_held"])


if __name__ == "__main__":
    unittest.main()
