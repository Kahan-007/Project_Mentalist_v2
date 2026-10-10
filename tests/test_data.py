import datetime as dt, json, os, sys, unittest, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from india_screener import config, news, prices, schedule, universe

IST = config.IST


def ts(day, hour=9, minute=15):
    return int(dt.datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=IST).timestamp())


def chart(days, closes, currency="INR", none_at=None):
    q = {"open": list(closes), "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
         "close": list(closes), "volume": [1000] * len(closes)}
    if none_at is not None:
        q["close"][none_at] = None
    return {"chart": {"result": [{"meta": {"currency": currency}, "timestamp": [ts(d) for d in days],
                                  "indicators": {"quote": [q], "adjclose": [{"adjclose": [c * 0.98 if c else c for c in closes]}]}}]}}


class Chart(unittest.TestCase):
    days = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]

    def test_excludes_today_before_eod_and_keeps_after(self):
        before = dt.datetime(2026, 9, 24, 14, 0, tzinfo=IST)
        after = dt.datetime(2026, 9, 24, 17, 0, tzinfo=IST)
        b1, _ = prices.parse_chart(chart(self.days, [100, 101, 102, 103]), before)
        b2, _ = prices.parse_chart(chart(self.days, [100, 101, 102, 103]), after)
        self.assertEqual(b1[-1]["date"], "2026-09-23")
        self.assertEqual(b2[-1]["date"], "2026-09-24")
        self.assertAlmostEqual(b2[-1]["adjclose"], 103 * 0.98)

    def test_rejects_non_inr_and_too_many_bad_bars(self):
        now = dt.datetime(2026, 9, 25, 17, tzinfo=IST)
        with self.assertRaises(prices.DataError):
            prices.parse_chart(chart(self.days, [1, 2, 3, 4], currency="USD"), now)
        with self.assertRaises(prices.DataError):
            prices.parse_chart(chart(self.days, [1, 2, 3, 4], none_at=1), now)   # 1 of 4 bad > 5%

    def test_skips_isolated_bad_bar(self):
        days = [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(40)]
        bars, problems = prices.parse_chart(chart(days, [100 + i for i in range(40)], none_at=5), dt.datetime(2026, 1, 1, tzinfo=IST))
        self.assertEqual(len(bars), 39)
        self.assertEqual(len(problems), 1)

    def test_symbol_mapping(self):
        self.assertEqual(prices.yahoo_symbol("INFY"), "INFY.NS")
        self.assertEqual(prices.yahoo_symbol("500209", "BSE"), "500209.BO")
        self.assertEqual(prices.yahoo_symbol("^NSEI"), "^NSEI")


class Names(unittest.TestCase):
    def test_short_names(self):
        self.assertEqual(universe.short_name("Coal India Limited"), "Coal India")
        self.assertEqual(universe.short_name("The Indian Hotels Company Limited"), "Indian Hotels")
        self.assertEqual(universe.short_name("Oil And Natural Gas Corporation Limited"), "Oil And Natural Gas")
        self.assertEqual(universe.short_name("Larsen & Toubro Ltd."), "Larsen & Toubro")

    def test_search_terms(self):
        self.assertEqual(news.search_terms("Oil And Natural Gas Corporation Limited", "ONGC"), ["Oil And Natural Gas", "ONGC"])
        self.assertEqual(news.search_terms("Larsen & Toubro Limited", "LT", ["L&T"]), ["Larsen & Toubro", "L&T"])

    def test_universe_csv(self):
        raw = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
               "INFY,Infosys Limited,EQ,08-FEB-1995,5,1,INE009A01021,5\nXYZ,Some Co,BE,01-JAN-2000,10,1,INE000,10\n").encode()
        recs = universe.parse_equity_csv(raw)
        self.assertEqual([r["symbol"] for r in recs], ["INFY"])
        self.assertEqual(recs[0]["isin"], "INE009A01021")


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Infosys bags $1.5 bn deal from European client - Economic Times</title><link>https://news.google.com/a</link>
<pubDate>Wed, 23 Sep 2026 06:00:00 GMT</pubDate><source url="x">Economic Times</source></item>
<item><title>Infosys bags $1.5 bn deal from European client - Mint</title><link>https://news.google.com/b</link>
<pubDate>Wed, 23 Sep 2026 07:00:00 GMT</pubDate><source url="x">Mint</source></item>
<item><title>IT stocks to watch this week - Moneycontrol</title><link>https://news.google.com/c</link>
<pubDate>Wed, 23 Sep 2026 07:00:00 GMT</pubDate><source url="x">Moneycontrol</source></item>
<item><title>Infosys old story</title><link>https://news.google.com/d</link>
<pubDate>Wed, 02 Sep 2026 07:00:00 GMT</pubDate><source url="x">X</source></item>
</channel></rss>"""


class Rss(unittest.TestCase):
    def test_filters_dedupes_and_tags(self):
        now = dt.datetime(2026, 9, 24, 12, tzinfo=dt.timezone.utc)
        items = news.parse_rss(RSS.encode(), ["Infosys"], now_utc=now, days=7)
        self.assertEqual(len(items), 1)                       # duplicate syndication, irrelevant and stale removed
        self.assertEqual(items[0]["categories"], ["deal/order"])
        self.assertNotIn(" - ", items[0]["title"])


class Schedule(unittest.TestCase):
    def test_task_xml_is_well_formed(self):
        xml = schedule.task_xml(r"C:\x & y\run_daily.cmd", r"C:\x & y", "18:30", r"PC\user")
        root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:StartWhenAvailable", ns).text, "true")
        self.assertIn("&", root.find(".//t:Command", ns).text)


if __name__ == "__main__":
    unittest.main()
