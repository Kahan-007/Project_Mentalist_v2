"""Dashboard API end-to-end on a real local socket, with market data replaced by fixtures."""
import json, os, sys, tempfile, threading, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import config, server
from tests.helpers import make_series
from tests import fixtures_fund as fx

UNIVERSE = [{"symbol": "TESTCO", "name": "Test Co Limited", "isin": "", "exchange": "NSE"},
            {"symbol": "OTHER", "name": "Completely Different Industries Limited", "isin": "", "exchange": "NSE"}]


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.patches = [mock.patch.object(config, k, v) for k, v in {
            "DATA": root, "CACHE": root / "cache", "REPORTS": root / "reports", "LOGS": root / "logs",
            "DB_PATH": root / "db.sqlite", "SHORTLIST_PATH": root / "shortlist.json", "CONFIG_PATH": root / "config.json"}.items()]
        stock, bench = make_series(n=320)
        cls.patches += [mock.patch("india_screener.prices.history", lambda s, e="NSE", **k: stock),
                        mock.patch("india_screener.prices.benchmark", lambda cfg=None: bench),
                        mock.patch("india_screener.universe.load", lambda refresh=False: UNIVERSE),
                        mock.patch("india_screener.news.fetch_news", lambda *a, **k: [
                            {"title": "Test Co <script>x</script> wins order", "url": "https://news.google.com/a", "source": "ET",
                             "published_utc": "2026-09-20T05:00:00+00:00", "categories": ["deal/order"]}])]
        for p in cls.patches:
            p.start()
        config.ensure_dirs()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def call(self, path, body=None, raw=False, headers=None):
        h = {"X-Screener": "1"} if body is not None else {}
        h.update(headers or {})
        data = None if body is None else (body if raw else json.dumps(body).encode())
        try:
            with urlopen(Request(self.base + path, data=data, headers=h), timeout=10) as r:
                return r.status, json.loads(r.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_1_pages_served(self):
        with urlopen(self.base + "/") as r:
            self.assertIn(b"Value Screener", r.read())
        with urlopen(self.base + "/app.js") as r:
            self.assertIn(b"function portfolio", r.read())

    def test_2_portfolio_flow(self):
        code, _ = self.call("/api/txn", {"kind": "BUY", "symbol": "testco", "date": "2025-01-02", "quantity": "10", "price": "500", "fees": "20"})
        self.assertEqual(code, 200)
        code, d = self.call("/api/portfolio")
        p = d["positions"][0]
        self.assertEqual((p["symbol"], p["quantity"], p["avg_cost"]), ("TESTCO", 10.0, 502.0))
        self.assertIsNotNone(p["value"])
        code, err = self.call("/api/txn", {"kind": "SELL", "symbol": "TESTCO", "date": "2025-02-01", "quantity": "11", "price": "1"})
        self.assertEqual(code, 400)
        self.assertIn("only 10 held", err["error"])

    def test_3_import_score_shortlist(self):
        body = fx.to_xlsx(fx.quality_company(), name="Test Co Ltd")
        code, d = self.call("/api/import/financials?symbol=TESTCO&basis=consolidated&sector=general&filename=t.xlsx", body, raw=True)
        self.assertEqual(code, 200, d)
        self.assertEqual(len(d["years"]), 10)
        code, d = self.call("/api/import/financials?symbol=OTHER&basis=consolidated&sector=general&filename=t.xlsx", body, raw=True)
        self.assertEqual(code, 400)                      # wrong company guard
        self.assertIn("not OTHER", d["error"])
        code, d = self.call("/api/manual", {"symbol": "TESTCO", "promoter_pct": "55", "pledge_pct": "0", "institutional_pct": "", "gnpa_pct": ""})
        self.assertEqual(code, 200)
        code, st = self.call("/api/stock?symbol=TESTCO")
        self.assertEqual(st["fundamentals"]["manual"]["promoter_pct"], 55.0)
        self.assertIn("buffett", st["score"])
        self.assertEqual(st["position"]["quantity"], 10.0)
        code, sc = self.call("/api/scores")
        self.assertEqual(sc["rows"][0]["symbol"], "TESTCO")
        code, r = self.call("/api/shortlist/add", {"symbol": "TESTCO", "aliases": ["TestCo", " "]})
        code, sl = self.call("/api/shortlist")
        self.assertEqual(sl["stocks"][0]["aliases"], ["TestCo"])
        self.assertIn("scores", sl["stocks"][0])

    def test_4_settings_validation(self):
        code, d = self.call("/api/settings", {"scoring": {"risk_free": "6.6"}})
        self.assertEqual(code, 400)
        code, d = self.call("/api/settings", {"scoring": {"risk_free": "0.066"}})
        self.assertEqual(d["scoring"]["risk_free"], 0.066)

    def test_5_security(self):
        code, _ = self.call("/api/txn", {"kind": "BUY"}, headers={"X-Screener": "0"})
        self.assertEqual(code, 403)
        code, _ = self.call("/api/txn", {"kind": "BUY"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        code, _ = self.call("/api/portfolio", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)
        code, _ = self.call("/api/stock?symbol=../../etc")
        self.assertEqual(code, 400)

    def test_7_bulk_auto_import_and_top250(self):
        real = sorted(Path(__file__).parent.glob("real_exports/*.xlsx"))
        if not real:
            self.skipTest("no real exports")
        for f in real[:3]:
            code, d = self.call("/api/import/auto?filename=" + f.name + "&basis=consolidated", f.read_bytes(), raw=True)
            self.assertEqual(code, 200, d)
            self.assertTrue(d["ok"], d)
        code, d = self.call("/api/import/auto?filename=x.csv&basis=consolidated", fx.to_csv(fx.quality_company(), "Zzqx Unknown Widgets Ltd"), raw=True)
        self.assertFalse(d["ok"])
        code, t = self.call("/api/top250")
        self.assertEqual(t["total"], 250)
        self.assertGreaterEqual(t["imported"], 3)

    def test_10_latest_prices_endpoint(self):
        real = sorted(Path(__file__).parent.glob("real_exports/*.xlsx"))
        if not real:
            self.skipTest("no real exports")
        self.call("/api/import/auto?filename=" + real[0].name + "&basis=consolidated", real[0].read_bytes(), raw=True)
        sym = self.call("/api/scores")[1]["rows"][0]["symbol"]
        extra = "".join(f"FAKE{i}.NS,{100+i}.0\n" for i in range(10))
        csv_text = f"Ticker,Latest_Price\n{sym}.NS,9999\n" + extra
        code, r = self.call("/api/import/latest-prices", csv_text.encode(), raw=True)
        self.assertEqual(code, 200)
        self.assertIn(sym, r["updated"])

    def test_9_manual_bulk(self):
        real = sorted(Path(__file__).parent.glob("real_exports/*.xlsx"))
        if not real:
            self.skipTest("no real exports")
        self.call("/api/import/auto?filename=" + real[0].name + "&basis=consolidated", real[0].read_bytes(), raw=True)
        code, r = self.call("/api/manual-bulk", {"rows": [
            {"symbol": "asianpaint", "promoter_pct": "52.6", "pledge_pct": ""},
            {"symbol": "NOPE", "promoter_pct": "10"},
            {"symbol": "TESTCO", "promoter_pct": "", "pledge_pct": ""}]})
        self.assertEqual(code, 200)
        self.assertIn("ASIANPAINT", r["saved"])
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(r["errors"][0]["symbol"], "NOPE")
        code, d = self.call("/api/stock?symbol=ASIANPAINT")
        self.assertEqual(d["fundamentals"]["manual"]["promoter_pct"], 52.6)
        # a later blank save must not clear an earlier value
        self.call("/api/manual-bulk", {"rows": [{"symbol": "ASIANPAINT", "pledge_pct": "0"}]})
        code, d = self.call("/api/stock?symbol=ASIANPAINT")
        self.assertEqual(d["fundamentals"]["manual"]["promoter_pct"], 52.6)
        self.assertEqual(d["fundamentals"]["manual"]["pledge_pct"], 0.0)

    def test_8_export_and_bulk_shortlist(self):
        real = sorted(Path(__file__).parent.glob("real_exports/*.xlsx"))
        if not real:
            self.skipTest("no real exports")
        for f in real:
            self.call("/api/import/auto?filename=" + f.name + "&basis=consolidated", f.read_bytes(), raw=True)
        code, t = self.call("/api/top250")
        by = {r["symbol"]: r for r in t["rows"]}
        self.assertEqual(by["TCS"]["bucket"], "quality_core")
        self.assertEqual(by["ONGC"]["bucket"], "lynch_only")
        self.assertIn("market_cap_cr", by["TCS"])
        with urlopen(self.base + "/export.csv?symbols=TCS,ONGC,HDFCBANK") as r:
            text = r.read().decode("utf-8-sig")
            self.assertIn("attachment", r.headers["Content-Disposition"])
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[1].startswith("TCS,"))
        self.assertIn("Quality core", lines[1])
        self.assertIn("not imported", lines[3])
        code, r = self.call("/api/shortlist/add-many", {"symbols": ["TCS", "INFY", "HDFCBANK"]})
        self.assertEqual(sorted(r["added"]), ["INFY", "TCS"])
        self.assertEqual(r["skipped_not_imported"], ["HDFCBANK"])
        code, sl = self.call("/api/shortlist")
        tcs = next(x for x in sl["stocks"] if x["symbol"] == "TCS")
        self.assertEqual(tcs["bucket"], "quality_core")

    def test_6_universe_search(self):
        code, d = self.call("/api/universe?q=diff")
        self.assertEqual([r["symbol"] for r in d], ["OTHER"])


if __name__ == "__main__":
    unittest.main()
