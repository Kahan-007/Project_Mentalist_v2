import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import fundamentals as fu, scoring as sc
from tests import fixtures_fund as fx

ST = dict(sc.DEFAULT_SETTINGS)


def rec(d, sector="general", basis="consolidated", manual=None):
    p = fu.parse_file(fx.to_csv(d), "x.csv")
    return {"symbol": "TEST", "basis": basis, "sector_type": sector, "manual": manual or {}, **p}


class Parse(unittest.TestCase):
    def test_csv_and_xlsx_give_identical_data(self):
        d = fx.quality_company()
        a = fu.parse_file(fx.to_csv(d), "a.csv")
        b = fu.parse_file(fx.to_xlsx(d), "a.xlsx")
        self.assertEqual(a["years"], fx.YEARS)
        self.assertEqual(a["years"], b["years"])
        for k in ("sales", "net_profit", "reserves", "cfo", "price", "shares_cr", "total"):
            for x, y in zip(a["series"][k], b["series"][k]):
                self.assertAlmostEqual(x, y, places=6)
        self.assertEqual(a["name"], "Test Co Ltd")
        self.assertAlmostEqual(a["series"]["total"][0], d["total"][0])      # first Total kept, not the assets one
        self.assertAlmostEqual(a["series"]["sales"][-1], d["sales"][-1])    # quarters section ignored

    def test_rejects_unrelated_file(self):
        with self.assertRaises(fu.ImportError_):
            fu.parse_file(b"a,b\n1,2\n", "x.csv")

    def test_template_round_trip(self):
        p = fu.parse_grid([r for r in __import__("csv").reader(fu.template_csv().splitlines())])
        self.assertEqual(len(p["years"]), 5)


class Metrics(unittest.TestCase):
    def test_known_values(self):
        d = fx.quality_company()
        m = fu.metrics(rec(d))
        self.assertAlmostEqual(m["eps_cagr_5"], 0.15, places=9)
        self.assertAlmostEqual(m["sales_cagr_10"], 0.15, places=9)
        self.assertAlmostEqual(m["pe"], 25.0, places=9)
        eq = [c + r for c, r in zip(d["cap"], d["res"])]
        self.assertAlmostEqual(m["roe"][3], d["np"][3] / ((eq[3] + eq[2]) / 2))
        capex3 = d["nb"][3] - d["nb"][2] + 0 + d["dep"][3]
        self.assertAlmostEqual(m["capex"][3], capex3)
        self.assertAlmostEqual(m["fcf"][3], d["cfo"][3] - capex3)
        self.assertEqual(m["losses"], 0)


class Scores(unittest.TestCase):
    def test_quality_beats_weak_on_every_philosophy(self):
        good, bad = sc.score_company(rec(fx.quality_company()), ST), sc.score_company(rec(fx.weak_company()), ST)
        for k in ("buffett", "munger", "lynch"):
            self.assertGreater(good[k]["score"], bad[k]["score"] + 25, k)
            self.assertTrue(0 <= good[k]["score"] <= 100)
        self.assertTrue(good["munger"]["matches"])
        self.assertFalse(bad["buffett"]["matches"])

    def test_weights_total_100(self):
        m = fu.metrics(rec(fx.quality_company()))
        for sector in ("general", "bank", "nbfc", "insurance"):
            r = rec(fx.quality_company(), sector=sector)
            for fn in (sc.buffett, sc.munger, sc.lynch):
                core = [c for c in fn(m, r, ST) if c.get("kind") != "penalty"]
                self.assertEqual(sum(c["weight"] for c in core), 100, (sector, fn.__name__))

    def test_expensive_price_lowers_value_criteria_only(self):
        cheap = sc.score_company(rec(fx.quality_company(price_mult=0.5)), ST)
        dear = sc.score_company(rec(fx.quality_company(price_mult=3.0)), ST)
        get = lambda r, p, k: next(c["score"] for c in r[p]["criteria"] if c["key"] == k)
        self.assertGreater(get(cheap, "buffett", "margin_of_safety"), get(dear, "buffett", "margin_of_safety"))
        self.assertGreater(get(cheap, "lynch", "peg"), get(dear, "lynch", "peg"))
        self.assertEqual(get(cheap, "buffett", "roe_consistency"), get(dear, "buffett", "roe_consistency"))

    def test_missing_manual_inputs_reduce_coverage_not_score(self):
        r1 = sc.score_company(rec(fx.quality_company()), ST)
        r2 = sc.score_company(rec(fx.quality_company(), manual={"promoter_pct": 60, "pledge_pct": 0, "institutional_pct": 20}), ST)
        self.assertLess(r1["lynch"]["coverage"], r2["lynch"]["coverage"])
        self.assertEqual(r2["lynch"]["coverage"], 1.0)

    def test_pledge_hurts(self):
        ok = sc.score_company(rec(fx.quality_company(), manual={"pledge_pct": 0}), ST)
        bad = sc.score_company(rec(fx.quality_company(), manual={"pledge_pct": 40}), ST)
        self.assertGreater(ok["munger"]["score"], bad["munger"]["score"])

    def test_bank_path_skips_industrial_criteria(self):
        r = sc.score_company(rec(fx.quality_company(), sector="bank", manual={"gnpa_pct": 2.5}), ST)
        keys = {c["key"] for p in ("buffett", "munger", "lynch") for c in r[p]["criteria"]}
        self.assertNotIn("capital_light", keys)
        self.assertNotIn("inventory", keys)
        self.assertIn("asset_quality", keys)

    def test_categories(self):
        self.assertEqual(sc.score_company(rec(fx.quality_company()), ST)["lynch_category"], "stalwart")
        self.assertEqual(sc.score_company(rec(fx.quality_company(g=0.25)), ST)["lynch_category"], "fast grower")
        self.assertEqual(sc.score_company(rec(fx.weak_company()), ST)["lynch_category"], "cyclical")

    def test_linear(self):
        self.assertEqual(sc.linear(5, 0, 10), 0.5)
        self.assertEqual(sc.linear(0.2, 1.0, 0.5), 1.0)       # lower is better
        self.assertEqual(sc.linear(0.75, 1.0, 0.5), 0.5)
        self.assertIsNone(sc.linear(None, 0, 1))


if __name__ == "__main__":
    unittest.main()
