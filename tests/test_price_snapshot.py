"""NSE bulk current-price tests (adapted from the other copy's test_price_snapshot.py)."""
import datetime as dt
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from india_screener import config, fundamentals, net, price_snapshot, workbook
from tests import fixtures_fund as fx


def udiff_zip(day="2026-09-24", count=600, infy=1234.5):
    rows = ["TradDt,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric"]
    rows.append(f"{day},INE009A01021,INFY,EQ,1,1,1,{infy}")
    rows.append(f"{day},INE000000002,INFY,BE,1,1,1,999")        # non-EQ series must be ignored
    rows += [f"{day},INE{i:09d},X{i:04d},EQ,1,1,1,{100 + i / 10:.2f}" for i in range(3, count + 2)]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("BhavCopy_NSE_CM.csv", "\n".join(rows) + "\n")
    return out.getvalue()


class PriceSnapshot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        patches = [mock.patch.object(config, k, v) for k, v in {
            "ROOT": root, "DATA": root, "CACHE": root / "cache", "REPORTS": root / "reports", "LOGS": root / "logs",
            "CONFIG_PATH": root / "config.json"}.items()]
        patches += [mock.patch.object(config, "now_ist", return_value=dt.datetime(2026, 9, 25, 19, 0, tzinfo=config.IST))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        config.ensure_dirs()
        fundamentals.save("INFY", fundamentals.parse_grid(fx.to_grid(fx.quality_company())), "consolidated", "general", "t.csv")

    def test_parse_prefers_eq_over_be(self):
        rows = price_snapshot.parse_zip(udiff_zip(), "2026-09-24")
        infy = [r for r in rows if r["symbol"] == "INFY"]
        self.assertEqual(infy, [{"symbol": "INFY", "price": 1234.5, "date": "2026-09-24", "series": "EQ", "isin": "INE009A01021"}])

    def test_matches_renamed_stock_by_unique_isin(self):
        row = {"symbol": "INFYNEW", "price": 1250, "date": "2026-09-25", "isin": "INE009A01021"}
        with mock.patch.object(price_snapshot.index250, "by_symbol", return_value={"INFY": {"isin": row["isin"]}}):
            self.assertEqual(price_snapshot.apply([row], "NSE bhavcopy close", ["INFY"])["updated"], ["INFY"])
        self.assertEqual(price_snapshot.saved_close("INFY", "NSE")["close"], 1250)
        self.assertIn("same ISIN", price_snapshot.saved_close("INFY", "NSE")["source"])

    def test_rejects_small_or_misdated_file(self):
        with self.assertRaisesRegex(ValueError, "EQ rows"):
            price_snapshot.parse_zip(udiff_zip(count=50), "2026-09-24")
        with self.assertRaisesRegex(ValueError, "expected"):
            price_snapshot.parse_zip(udiff_zip(day="2026-09-23"), "2026-09-24")

    def test_current_price_only_history_untouched(self):
        before = list(fundamentals.load("INFY")["series"]["price"])
        rows = price_snapshot.parse_zip(udiff_zip(), "2026-09-24")
        r = price_snapshot.apply(rows, "NSE", ["INFY"])
        rec = fundamentals.load("INFY")
        self.assertEqual(r["updated"], ["INFY"])
        self.assertEqual((rec["meta"]["price"], rec["meta"]["price_date"]), (1234.5, "2026-09-24"))
        self.assertEqual(rec["series"]["price"], before)

    def test_older_price_never_overwrites_newer(self):
        workbook.update_current_price("INFY", 200, "2026-09-25", source="new")
        r = price_snapshot.apply(price_snapshot.parse_zip(udiff_zip(), "2026-09-24"), "old", ["INFY"])
        self.assertEqual(r["stale_not_overwritten"], ["INFY"])
        self.assertEqual(fundamentals.load("INFY")["meta"]["price"], 200)

    def test_walks_back_over_holiday(self):
        with mock.patch.object(net, "fetch", side_effect=[net.FetchError("HTTP 404"), udiff_zip()]) as f:
            rows, url = price_snapshot.fetch_nse()
        self.assertIn("20260924", url)
        self.assertEqual(f.call_count, 2)

    def test_nse_failure_falls_back_to_yahoo(self):
        bars = [{"date": "2026-09-25", "close": 1500.0}]
        with mock.patch.object(net, "fetch", side_effect=net.FetchError("down")), \
             mock.patch.object(price_snapshot.prices, "history", return_value=bars):
            r = price_snapshot.refresh(["INFY"], cfg=config.load_config())
        self.assertTrue(r["nse_error"])
        self.assertEqual(r["yahoo_updated"], ["INFY"])
        self.assertEqual(fundamentals.load("INFY")["meta"]["price"], 1500.0)

    def test_stale_yahoo_cache_does_not_count_as_current(self):
        older = [{"date": "2026-09-24", "close": 1500.0}]
        with mock.patch.object(price_snapshot, "fetch_nse", return_value=(
                [{"symbol": "INFY", "date": "2026-09-24", "price": 1400.0}], "archive")), \
             mock.patch.object(price_snapshot.prices, "history", return_value=older) as history:
            result = price_snapshot.refresh(["INFY"], benchmark_date="2026-09-25")
        self.assertEqual(result["current_count"], 0)
        self.assertIn("INFY", result["yahoo_failed"])
        self.assertTrue(history.call_args.kwargs["force"])
        self.assertEqual(price_snapshot.status()["session"], "2026-09-25")
        self.assertEqual(fundamentals.load("INFY")["meta"]["price"], 1400)

    def test_close_is_retained_without_share_count(self):
        rows = [{"symbol": "INFY", "date": "2026-09-25", "price": 1500.0}]
        with mock.patch.object(price_snapshot, "fetch_nse", return_value=(rows, "archive")), \
             mock.patch.object(workbook.financial_math, "current_shares", return_value=(None, None)):
            result = price_snapshot.refresh(["INFY"])
        self.assertEqual(result["current_count"], 1)
        self.assertEqual(result["no_share_count"], ["INFY"])
        self.assertIsNone(fundamentals.load("INFY")["meta"]["market_cap"])

    def test_nse_lagging_benchmark_uses_yahoo(self):
        bars = [{"date": "2026-09-25", "close": 1500.0}]
        with mock.patch.object(net, "fetch", return_value=udiff_zip(day="2026-09-25")), \
             mock.patch.object(price_snapshot, "candidate_dates", return_value=[dt.date(2026, 9, 25)]), \
             mock.patch.object(price_snapshot.prices, "history", return_value=bars):
            r = price_snapshot.refresh(["INFY"], cfg=config.load_config(), benchmark_date="2026-09-26")
        self.assertIn("Nifty", r["nse_error"])


if __name__ == "__main__":
    unittest.main()
