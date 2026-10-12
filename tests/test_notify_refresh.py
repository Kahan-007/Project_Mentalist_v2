"""Notification centre, results-season merge rules, ROIC and source descriptions."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from india_screener import appnotify, config, daily, fundamentals, screener_fetch, sources
from tests import fixtures_fund as fx


class TempData(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for k, v in {"ROOT": root, "DATA": root, "CACHE": root / "cache", "REPORTS": root / "reports", "LOGS": root / "logs",
                     "DB_PATH": root / "db.sqlite", "CONFIG_PATH": root / "config.json"}.items():
            p = mock.patch.object(config, k, v)
            p.start()
            self.addCleanup(p.stop)
        config.ensure_dirs()


class PhoneChannels(TempData):
    def test_ntfy_and_discord_payloads(self):
        from india_screener import notify
        cfg = config.load_config()
        cfg["notify"].update(windows_toast=False, ntfy_topic="screener-test123",
                             discord_webhook="https://discord.com/api/webhooks/1/abc")
        with mock.patch.object(notify.net, "post_json") as post:
            problems = notify.send("INFY: drop ₹", "body text", cfg)
        self.assertEqual(problems, [])
        (u1, p1), (u2, p2) = [c.args for c in post.call_args_list]
        self.assertEqual((u1, p1["topic"], p1["title"]), ("https://ntfy.sh/", "screener-test123", "INFY: drop ₹"))
        self.assertEqual(u2, "https://discord.com/api/webhooks/1/abc")
        self.assertTrue(p2["content"].startswith("**INFY: drop ₹**"))

    def test_bad_values_rejected(self):
        import json
        from india_screener import server
        with self.assertRaises(ValueError):
            server.api_settings_save({}, json.dumps({"discord_webhook": "https://evil.example/hook"}).encode())
        with self.assertRaises(ValueError):
            server.api_settings_save({}, json.dumps({"ntfy_topic": "has space"}).encode())
        server.api_settings_save({}, json.dumps({"ntfy_topic": "screener-ok_123"}).encode())
        self.assertEqual(config.load_config()["notify"]["ntfy_topic"], "screener-ok_123")


class Notifications(TempData):
    def test_kinds_dedupe_and_read(self):
        appnotify.stock("INFY: drop", "detail", symbol="INFY")
        appnotify.program("NSE down", "first", key="nse")
        appnotify.program("NSE down", "second", key="nse")          # same day, same key: merged, not duplicated
        d = appnotify.items()
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual(d["unread_by_kind"], {"stock": 1, "program": 1})
        self.assertEqual([n["body"] for n in d["items"] if n["kind"] == "program"], ["second"])
        appnotify.mark_read([d["items"][0]["id"]])
        self.assertEqual(appnotify.unread_count(), 1)
        appnotify.mark_read()
        self.assertEqual(appnotify.unread_count(), 0)

    def test_errors_are_explained(self):
        title, body = daily.explain_error("current prices (NSE)", "HTTP 404")
        self.assertIn("NSE", title)
        self.assertIn("Yahoo", body)
        self.assertIn("retried", daily.explain_error("autofill:gnpa", "boom")[1])

    def test_schedule_health(self):
        (config.REPORTS / "2026-09-18.md").write_text("x")
        with mock.patch.object(config, "now_ist", return_value=dt.datetime(2026, 9, 25, 20, 0, tzinfo=config.IST)):
            appnotify.check_schedule_health()
        self.assertIn("hasn't run", appnotify.items("program")["items"][0]["title"])


def rec(years, quarters=None, source="Top 250 workbook (Screener tables retrieved 2026-09-24)"):
    r = {"symbol": "TESTCO", "name": "Test", "basis": "consolidated", "sector_type": "general", "source": source,
         "years": years, "series": {"price": [100.0] * len(years), "sales": [1.0] * len(years)},
         "meta": {"shares": 1e8, "price": 50.0, "price_date": "2026-09-25", "price_source": "NSE bhavcopy close x shares"},
         "quarters": {"dates": quarters or []}, "manual": {"pledge_pct": 0.0}, "manual_source": {"pledge_pct": "NSE"}}
    return r


class Refresh(unittest.TestCase):
    def test_new_year_merged_keeping_manual_prices_and_close(self):
        old = rec(["2024-03-31", "2025-03-31"])
        new = rec(["2024-03-31", "2025-03-31", "2026-03-31"], ["2026-06-30"], source="Screener.in public page x (retrieved 2026-09-26)")
        new.update(manual={}, manual_source={})
        new["series"]["price"] = [None] * 3
        new["meta"] = {"shares": 2e8, "price": 10.0}
        out, what = screener_fetch.merge_refresh(new, copy.deepcopy(old))
        self.assertIn("new annual results 2026", what)
        self.assertEqual(out["manual"], {"pledge_pct": 0.0})
        self.assertEqual(out["series"]["price"], [100.0, 100.0, None])      # old year-end prices kept, new year to fill
        self.assertEqual((out["meta"]["price"], out["meta"]["market_cap"]), (50.0, 50.0 * 20))   # fresh close x new shares

    def test_never_goes_backwards_and_keeps_exports(self):
        old = rec(["2025-03-31", "2026-03-31"])
        self.assertIsNone(screener_fetch.merge_refresh(rec(["2024-03-31", "2025-03-31"]), old)[0])
        export = rec(["2025-03-31", "2026-03-31"], ["2026-03-31"], source="Infosys.xlsx")
        out, reason = screener_fetch.merge_refresh(rec(["2025-03-31", "2026-03-31"], ["2026-06-30"]), export)
        self.assertEqual(out["quarters"]["dates"], ["2026-06-30"])
        self.assertEqual(out["series"], export["series"])
        self.assertEqual(out["source"], "Infosys.xlsx")


class ReturnsAndSources(unittest.TestCase):
    def test_roic_and_roce(self):
        r = fundamentals.parse_grid(fx.to_grid(fx.quality_company()))
        r.update(symbol="T", sector_type="general", basis="consolidated", source="t.xlsx", imported_at="2026-09-01")
        m = fundamentals.metrics(r)
        i = len(m["years"]) - 1
        s = r["series"]
        ce = lambda j: s["share_capital"][j] + s["reserves"][j] + (s["borrowings"][j] or 0)
        ebit = s["pbt"][i] + s["interest"][i]
        self.assertAlmostEqual(m["roce"][i], ebit / ((ce(i) + ce(i - 1)) / 2))
        ic = lambda j: ce(j) - (s["cash"][j] or 0)
        t = min(0.4, max(0.0, s["tax"][i] / s["pbt"][i]))
        self.assertAlmostEqual(m["roic"][i], ebit * (1 - t) / ((ic(i) + ic(i - 1)) / 2))

    def test_source_text(self):
        r = rec(["2026-03-31"])
        t = sources.for_record(r)
        self.assertIn("NSE official closing price, 25-Sep-2026", t["price"])
        self.assertIn("Top 250 workbook", t["financials"])
        self.assertEqual(t["manual"]["pledge_pct"], "Promoter pledge: NSE")


if __name__ == "__main__":
    unittest.main()
