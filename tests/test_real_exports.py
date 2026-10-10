"""Regression tests on real Screener.in exports (tests/real_exports). Skipped if the folder is empty."""
import copy, glob, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import fundamentals as fu, index250, scoring as sc, verify

FILES = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "real_exports", "*.xlsx")))
ST = dict(sc.DEFAULT_SETTINGS)


def load(path):
    parsed = fu.parse_file(__import__("pathlib").Path(path).read_bytes(), os.path.basename(path))
    sym, conf, _ = index250.match(parsed["name"])
    ix = index250.by_symbol()[sym]
    return {"symbol": sym, "basis": "consolidated", "sector_type": ix["suggested_type"], "industry": ix["industry"],
            "manual": {}, **parsed}, conf


@unittest.skipUnless(FILES, "no real exports present")
class RealExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recs = {}
        for f in FILES:
            rec, conf = load(f)
            cls.recs[rec["symbol"]] = (rec, conf)

    def test_structure(self):
        for sym, (rec, conf) in self.recs.items():
            self.assertGreaterEqual(conf, 0.9, sym)
            self.assertEqual(rec["years"], sorted(rec["years"]), sym)
            self.assertGreaterEqual(len(rec["years"]), 4, sym)
            for k in ("sales", "net_profit", "reserves", "cfo", "price", "shares_cr"):
                self.assertTrue(any(v is not None for v in rec["series"][k]), (sym, k))
            self.assertTrue(rec["meta"].get("market_cap"), sym)
            self.assertEqual(len(rec["quarters"]["dates"]), len(rec["quarters"]["net_profit"]), sym)

    def test_internal_checks_mostly_pass(self):
        for sym, (rec, _) in self.recs.items():
            res = {c["check"]: c["status"] for c in verify.check_export(rec)}
            self.assertEqual(res.get("Balance sheet balances"), "ok", sym)
            self.assertEqual(res.get("Cash flows add up"), "ok", sym)

    def test_every_company_scores(self):
        for sym, (rec, _) in self.recs.items():
            r = sc.score_company(rec, ST)
            for k in ("buffett", "munger", "lynch"):
                self.assertIsNotNone(r[k]["score"], (sym, k))
                self.assertTrue(0 <= r[k]["score"] <= 100, (sym, k))
            short = copy.deepcopy(rec)
            short["years"] = short["years"][-4:]
            short["series"] = {a: v[-4:] for a, v in short["series"].items()}
            r4 = sc.score_company(short, ST)
            self.assertTrue(r4["provisional"], sym)

    def test_known_characteristics(self):
        get = lambda s: self.recs[s][0] if s in self.recs else None
        if get("SBILIFE"):
            self.assertEqual(get("SBILIFE")["sector_type"], "insurance")
        if get("ONGC"):
            self.assertEqual(sc.score_company(get("ONGC"), ST)["lynch_category"], "cyclical")
        if get("POWERGRID"):
            self.assertIn("Low debt / equity", sc.score_company(get("POWERGRID"), ST)["buffett"]["failed_essentials"])
        if get("TCS"):
            m = fu.metrics(get("TCS"))
            self.assertGreater(m["roe"][-1], 0.35)
            self.assertIsNotNone(m["ttm"])


if __name__ == "__main__":
    unittest.main()
