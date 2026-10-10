"""End-to-end daily job with the network replaced by fixtures."""
import datetime as dt, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import config, daily, portfolio, shortlist, tracker
from tests.helpers import make_series


class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(config, k, v) for k, v in {
            "DATA": root, "CACHE": root / "cache", "REPORTS": root / "reports", "LOGS": root / "logs",
            "DB_PATH": root / "db.sqlite", "SHORTLIST_PATH": root / "shortlist.json", "CONFIG_PATH": root / "none.json"}.items()]
        for p in self.patches:
            p.start()
        self.stock, self.bench = make_series(n=330, shocks={300: -0.09}, calm_after=302, seed=11)
        self.event_day = self.stock[300]["date"]
        shortlist.add("INFY", aliases=["Infosys"])
        portfolio.add("BUY", "TCS", "2025-01-02", quantity=5, price="1000")
        self.visible = 303

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def fake_history(self, symbol, exchange="NSE", **k):
        return self.stock[: self.visible] if symbol == "INFY" else self.bench[: self.visible]

    def fake_news(self, symbol, name, aliases=(), days=7):
        if symbol != "INFY":
            return []
        return [{"title": "Infosys faces SEBI probe", "url": "https://news.google.com/x", "source": "ET",
                 "published_utc": f"{self.event_day}T05:00:00+00:00", "categories": ["legal/regulatory"]}]

    def run_daily(self):
        with mock.patch("india_screener.prices.history", self.fake_history), \
             mock.patch("india_screener.prices.benchmark", lambda cfg=None: self.bench[: self.visible]), \
             mock.patch("india_screener.news.fetch_news", self.fake_news), \
             mock.patch("india_screener.universe.load", lambda refresh=False: [{"symbol": "TCS", "name": "Tata Consultancy Services Limited"}]), \
             mock.patch("india_screener.autofill.run", lambda: {}), \
             mock.patch("india_screener.price_snapshot.refresh", lambda **k: {"nse_error": None, "yahoo_failed": {}}), \
             mock.patch("india_screener.notify.send") as send:
            res = daily.run(send_alerts=True)
            return res, [c.args[0] for c in send.call_args_list]

    def test_lifecycle(self):
        res, titles = self.run_daily()
        self.assertEqual(res["errors"], {})
        self.assertEqual(res["tracked"], 2)
        self.assertEqual(len(titles), 1)
        self.assertIn("abnormal drop", titles[0])
        self.assertEqual(tracker.list_events()[0]["status"], "dip_confirmed")

        self.visible = 330                       # 27 more sessions arrive
        res, titles = self.run_daily()
        self.assertEqual(len(titles), 1)
        self.assertIn("levelled out", titles[0])
        self.assertEqual(tracker.list_events()[0]["status"], "levelled")

        res, titles = self.run_daily()           # nothing new: no duplicate alerts
        self.assertEqual(titles, [])
        report = Path(res["report"]).read_text(encoding="utf-8")
        self.assertIn("| TCS | 5 |", report)
        self.assertIn("## Financial analysis (holdings and shortlist)", report)
        self.assertIn("| TCS | held |", report)   # holdings are analysed too, even when not shortlisted
        from india_screener import recommendations
        logged = recommendations.history(full=True)
        self.assertEqual([r["reason"] for r in logged], ["alert_levelled", "alert_dip"])   # newest first, one per alert
        self.assertIn("abnormal drop", logged[1]["note"])


if __name__ == "__main__":
    unittest.main()
