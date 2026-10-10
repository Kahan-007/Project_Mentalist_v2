import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import index250, verify


class Index(unittest.TestCase):
    def test_list_and_types(self):
        u = index250.by_symbol()
        self.assertEqual(len(u), 250)
        self.assertEqual(u["HDFCBANK"]["suggested_type"], "bank")
        self.assertEqual(u["SBILIFE"]["suggested_type"], "insurance")
        self.assertEqual(u["BAJFINANCE"]["suggested_type"], "nbfc")
        self.assertEqual(u["HDFCAMC"]["suggested_type"], "general")
        self.assertEqual(u["TCS"]["suggested_type"], "general")

    def test_every_company_matches_itself(self):
        for r in index250.load():
            sym, conf, _ = index250.match(r["name"].upper().replace(".", ""))
            self.assertEqual(sym, r["symbol"], r["name"])
            self.assertGreaterEqual(conf, 0.6, r["name"])

    def test_screener_style_names(self):
        self.assertEqual(index250.match("OIL & NATURAL GAS CORPN LTD")[0], "ONGC")
        self.assertEqual(index250.match("SBI LIFE INSURAN")[0], "SBILIFE")
        self.assertEqual(index250.match("PIDILITE INDUSTRIES LTD")[0], "PIDILITIND")

    def test_rejects_non_index_csv(self):
        with self.assertRaises(ValueError):
            index250.parse("a,b\n1,2\n")


class Verify(unittest.TestCase):
    rec = {"years": ["2024-03-31", "2025-03-31"], "series": {"price": [100.0, 120.0]}}

    def bars(self, p1, p2):
        return [{"date": "2024-03-28", "close": p1}, {"date": "2025-03-28", "close": p2}]

    def test_prices_agree(self):
        self.assertEqual(verify.check_prices(self.rec, self.bars(101, 119))[0]["status"], "ok")

    def test_split_mismatch_detected(self):
        r = verify.check_prices(self.rec, self.bars(200, 120))[0]
        self.assertEqual(r["status"], "warn")
        self.assertIn("split/bonus", r["detail"])

    def test_universe_cross_check(self):
        idx = [{"symbol": "AAA", "isin": "IN1"}, {"symbol": "OLD", "isin": "IN2"}]
        nse = [{"symbol": "AAA", "isin": "IN1"}, {"symbol": "NEW", "isin": "IN2"}]
        res = verify.check_universe(idx, nse)
        self.assertEqual(len(res), 1)
        self.assertIn("NEW", res[0]["detail"])


if __name__ == "__main__":
    unittest.main()
