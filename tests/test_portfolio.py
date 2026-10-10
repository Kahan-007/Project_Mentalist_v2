import os, sys, tempfile, unittest
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import portfolio as pf


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, *a, **k):
        return pf.add(*a, path=self.db, **k)

    def test_fifo_realised_pnl_with_fees(self):
        self.add("BUY", "INFY", "2025-01-01", quantity=10, price="100", fees="10")    # cost 1010
        self.add("BUY", "INFY", "2025-02-01", quantity=10, price="120", fees="10")    # cost 1210
        self.add("SELL", "INFY", "2025-03-01", quantity=15, price="130", fees="15")   # proceeds 1935
        p = pf.positions(self.db)["INFY"]
        # cost of 15 = 1010 + 5/10*1210 = 1615 ; realised = 1935 - 1615 = 320
        self.assertEqual(p["realised"], Decimal("320"))
        self.assertEqual(p["quantity"], Decimal("5"))
        self.assertEqual(p["invested"], Decimal("605"))
        self.assertEqual(p["avg_cost"], Decimal("121"))

    def test_split_and_bonus_keep_cost(self):
        self.add("BUY", "TCS", "2025-01-01", quantity=10, price="500")
        self.add("SPLIT", "TCS", "2025-02-01", ratio="5:1")
        self.add("BONUS", "TCS", "2025-03-01", ratio="1:1")
        p = pf.positions(self.db)["TCS"]
        self.assertEqual(p["quantity"], Decimal("100"))
        self.assertEqual(p["invested"], Decimal("5000"))
        self.assertEqual(p["avg_cost"], Decimal("50"))

    def test_fractional_bonus_rejected_and_rolled_back(self):
        self.add("BUY", "ITC", "2025-01-01", quantity=3, price="400")
        with self.assertRaises(pf.LedgerError):
            self.add("BONUS", "ITC", "2025-02-01", ratio="1:2")
        self.assertEqual(len(pf.transactions(self.db)), 1)

    def test_oversell_rejected_and_rolled_back(self):
        self.add("BUY", "SBIN", "2025-01-01", quantity=5, price="600")
        with self.assertRaises(pf.LedgerError):
            self.add("SELL", "SBIN", "2025-01-05", quantity=6, price="610")
        self.assertEqual(len(pf.transactions(self.db)), 1)

    def test_backdated_entry_replays_in_date_order(self):
        self.add("BUY", "LT", "2025-02-01", quantity=2, price="10")
        self.add("BUY", "LT", "2025-01-01", quantity=1, price="5")
        self.add("SELL", "LT", "2025-03-01", quantity=1, price="10")
        self.assertEqual(pf.positions(self.db)["LT"]["realised"], Decimal("5"))   # oldest (5) sold first

    def test_dividends_and_validation(self):
        self.add("BUY", "COALINDIA", "2025-01-01", quantity=10, price="400")
        self.add("DIVIDEND", "COALINDIA", "2025-02-01", amount="152.5")
        self.assertEqual(pf.positions(self.db)["COALINDIA"]["dividends"], Decimal("152.5"))
        for bad in [dict(quantity="1.5", price="10"), dict(quantity=1, price="-1"), dict(quantity=1, price="abc")]:
            with self.assertRaises(pf.LedgerError):
                self.add("BUY", "COALINDIA", "2025-01-01", **bad)
        with self.assertRaises(pf.LedgerError):
            self.add("BUY", "COALINDIA", "01/02/2025", quantity=1, price=1)

    def test_zerodha_import_is_idempotent(self):
        csv_text = ("symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time\n"
                    "INFY,INE009A01021,2025-01-02,NSE,EQ,EQ,buy,false,10.000000,1500.50,111,9001,2025-01-02T10:00:00\n"
                    "INFY,INE009A01021,2025-01-03,NSE,EQ,EQ,sell,false,4.000000,1550.00,112,9002,2025-01-03T11:00:00\n"
                    "NIFTY25JANFUT,,2025-01-03,NFO,FO,,buy,false,50,23000,113,9003,2025-01-03T11:00:00\n")
        r1 = pf.import_zerodha_tradebook(csv_text, path=self.db)
        r2 = pf.import_zerodha_tradebook(csv_text, path=self.db)
        self.assertEqual(r1, {"added": 2, "skipped": 1, "symbols": ["INFY"]})
        self.assertEqual(r2, {"added": 0, "skipped": 3, "symbols": []})
        p = pf.positions(self.db)["INFY"]
        self.assertEqual(p["quantity"], Decimal("6"))
        self.assertEqual(p["realised"], Decimal("4") * (Decimal("1550") - Decimal("1500.50")))


if __name__ == "__main__":
    unittest.main()
