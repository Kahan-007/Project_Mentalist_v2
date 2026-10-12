"""Recommendation log: what gets recorded, what it freezes, and that old rows never change."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from india_screener import config, fundamentals, recommendations, server
from tests import fixtures_fund as fx


class RecommendationLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for k, v in {"ROOT": root, "DATA": root, "CACHE": root / "cache", "REPORTS": root / "reports", "LOGS": root / "logs",
                     "DB_PATH": root / "db.sqlite", "SHORTLIST_PATH": root / "shortlist.json",
                     "CONFIG_PATH": root / "config.json"}.items():
            p = mock.patch.object(config, k, v)
            p.start()
            self.addCleanup(p.stop)
        config.ensure_dirs()
        fundamentals.save("TESTCO", fundamentals.parse_grid(fx.to_grid(fx.quality_company())), "consolidated", "general", "t.csv")

    def test_shortlisting_freezes_the_analysis(self):
        server.api_shortlist_add({}, json.dumps({"symbol": "TESTCO"}).encode())
        server.api_shortlist_add({}, json.dumps({"symbol": "TESTCO", "aliases": ["Test"]}).encode())  # edit, not a new listing
        rows = recommendations.history(full=True)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["reason"], r["shortlisted"], r["held"]), ("shortlisted", 1, 0))
        self.assertIsNotNone(r["buffett"])
        self.assertTrue(r["analysis"]["buffett"]["criteria"])     # every criterion kept, not just the totals

    def test_old_rows_do_not_change_when_data_changes(self):
        server.api_shortlist_add({}, json.dumps({"symbol": "TESTCO"}).encode())
        before = recommendations.history(full=True)[0]
        fundamentals.set_manual("TESTCO", pledge_pct=50)   # a later change that moves Munger's score
        server.api_shortlist_remove({}, json.dumps({"symbol": "TESTCO"}).encode())
        rows = recommendations.history(full=True)
        self.assertEqual([r["reason"] for r in rows], ["removed_from_shortlist", "shortlisted"])
        self.assertEqual(rows[1], before)
        self.assertNotEqual(rows[0]["munger"], before["munger"])

    def test_buy_and_sell_are_recorded(self):
        server.api_txn({}, json.dumps({"kind": "BUY", "symbol": "TESTCO", "date": "2026-09-01", "quantity": "10", "price": "100"}).encode())
        server.api_txn({}, json.dumps({"kind": "DIVIDEND", "symbol": "TESTCO", "date": "2026-09-02", "amount": "5"}).encode())
        rows = recommendations.history(full=True)
        self.assertEqual([r["reason"] for r in rows], ["bought"])
        self.assertEqual(rows[0]["held"], 1)
        self.assertIn("BUY 10 @ 100", rows[0]["note"])

    def test_stock_without_financials_is_still_logged(self):
        recommendations.record("NOFIN", "shortlisted")
        r = recommendations.history(full=True)[0]
        self.assertIsNone(r["buffett"])
        self.assertIn("financials not imported", r["note"])

    def test_csv_export(self):
        server.api_shortlist_add({}, json.dumps({"symbol": "TESTCO"}).encode())
        text = recommendations.to_csv(recommendations.history())
        self.assertTrue(text.startswith("logged_at,symbol,reason"))
        self.assertIn("TESTCO", text)


if __name__ == "__main__":
    unittest.main()
