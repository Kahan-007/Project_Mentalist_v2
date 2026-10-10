"""Tests on the real combined workbook (tests/real_workbook). Skipped if absent."""
import glob, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import config, fundamentals as fu, index250, scoring as sc, workbook

HERE = Path(__file__).parent
WB = sorted(HERE.glob("real_workbook/*.xlsm"))
EXPORTS = sorted(HERE.glob("real_exports/*.xlsx"))
ST = dict(sc.DEFAULT_SETTINGS)


@unittest.skipUnless(WB, "no workbook present")
class Workbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.patches = [mock.patch.object(config, k, v) for k, v in {
            "DATA": root, "CACHE": root / "cache", "REPORTS": root / "reports", "LOGS": root / "logs",
            "DB_PATH": root / "db.sqlite", "SHORTLIST_PATH": root / "shortlist.json", "CONFIG_PATH": root / "c.json"}.items()]
        for p in cls.patches:
            p.start()
        config.ensure_dirs()
        cls.result = workbook.import_workbook(WB[0].read_bytes())

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_counts(self):
        self.assertEqual(self.result["universe"], 250)
        self.assertGreaterEqual(len(self.result["imported"]), 240)
        self.assertTrue(all("year" in s["reason"] for s in self.result["skipped"]))

    def test_matches_single_company_exports(self):
        for f in EXPORTS:
            parsed = fu.parse_file(f.read_bytes(), f.name)
            sym = index250.match(parsed["name"])[0]
            wb = fu.load(sym)
            if not wb:
                continue
            ex = {"symbol": sym, "basis": "consolidated", "sector_type": "general", "manual": {}, **parsed}
            a, b = fu.metrics(ex), fu.metrics(wb)
            self.assertEqual(a["years"], b["years"], sym)
            for k in ("roe", "eps", "ebit_margin", "de_ratio"):
                for x, y in zip(a[k], b[k]):
                    if x is not None and y is not None and abs(x) > 1e-6:
                        self.assertLess(abs(x - y) / abs(x), 0.06, (sym, k))

    def test_shares_are_split_adjusted(self):
        # Profit-for-EPS / EPS reproduces Screener's split/bonus-adjusted share count (Reliance had two 1:1 bonuses).
        for f in EXPORTS:
            parsed = fu.parse_file(f.read_bytes(), f.name)
            sym = index250.match(parsed["name"])[0]
            wb = fu.load(sym)
            if not wb:
                continue
            for x, y in zip(parsed["series"]["shares_cr"], wb["series"]["shares_cr"]):
                if x and y:
                    self.assertLess(abs(x - y) / x, 0.01, sym)

    def test_transition_period_annualised(self):
        nes = fu.load("NESTLEIND")
        self.assertTrue(any("15 months" in n for n in nes["period_notes"]))

    def test_lender_needs_npa_to_match(self):
        rec = fu.load("HDFCBANK")
        r = sc.score_company(rec, ST)
        self.assertEqual(rec["sector_type"], "bank")
        self.assertIn("Gross NPA (manual input)", r["buffett"]["unknown_essentials"])
        self.assertFalse(r["buffett"]["matches"])

    def test_fill_prices(self):
        rec = fu.load("TCS")
        bars = [{"date": d, "close": 1000.0 + i} for i, d in enumerate(rec["years"])]
        bars.append({"date": "2026-09-24", "close": 3000.0})
        self.assertTrue(workbook.fill_prices("TCS", bars))
        rec = fu.load("TCS")
        self.assertEqual(rec["series"]["price"][0], 1000.0)
        self.assertAlmostEqual(rec["meta"]["market_cap"], 3000.0 * rec["series"]["shares_cr"][-1])
        self.assertIsNotNone(sc.score_company(rec, ST)["buffett"]["criteria"][-1]["score"])

    def test_latest_prices_snapshot(self):
        extra = "".join(f"FAKE{i}.NS,{100+i}.0\n" for i in range(10))
        csv_text = "Ticker,Latest_Price\nTCS.NS,3000.00\nINFY.NS,2000.00\nZZZNOTREAL.NS,50.00\n" + extra
        r = workbook.apply_latest_prices(csv_text)
        self.assertIn("TCS", r["updated"])
        self.assertEqual(r["not_yet_imported"], 11)
        rec = fu.load("TCS")
        self.assertEqual(rec["meta"]["price"], 3000.0)
        self.assertAlmostEqual(rec["meta"]["market_cap"], 3000.0 * rec["series"]["shares_cr"][-1])
        self.assertEqual(rec["meta"]["price_source"], "latest price snapshot (user-provided)")

    def test_latest_prices_rejects_bad_csv(self):
        with self.assertRaises(fu.ImportError_):
            workbook.apply_latest_prices("a,b\n1,2\n")

    def test_every_record_scores(self):
        for sym in fu.all_symbols():
            r = sc.score_company(fu.load(sym), ST)
            for k in ("buffett", "munger", "lynch"):
                s = r[k]["score"]
                self.assertTrue(s is None or 0 <= s <= 100, (sym, k, s))


if __name__ == "__main__":
    unittest.main()
