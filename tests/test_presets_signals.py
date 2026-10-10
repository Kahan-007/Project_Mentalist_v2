"""Scoring presets / undo / redo / reset, the Buy-Sell-Held signal, and the Screener page reader."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from india_screener import config, presets, recommendations, scoring, screener_fetch, server


class Presets(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for target, k, v in [(config, "DATA", root), (config, "CONFIG_PATH", root / "config.json")]:
            p = mock.patch.object(target, k, v)
            p.start()
            self.addCleanup(p.stop)

    def save(self, **sc):
        server.api_settings_save({}, json.dumps({"scoring": sc}).encode())

    def test_undo_redo_reset(self):
        self.save(pass_score=70)
        self.save(pass_score=75)
        self.assertEqual(scoring.settings()["pass_score"], 75)
        presets.undo()
        self.assertEqual(scoring.settings()["pass_score"], 70)
        presets.undo()
        self.assertEqual(scoring.settings()["pass_score"], 65)          # the starting settings
        with self.assertRaises(ValueError):
            presets.undo()
        presets.redo()
        self.assertEqual(scoring.settings()["pass_score"], 70)
        self.save(pass_score=80)                                         # a new change drops the redo branch
        self.assertFalse(presets.state()["can_redo"])
        presets.reset()
        self.assertEqual(scoring.settings()["pass_score"], 65)
        presets.undo()
        self.assertEqual(scoring.settings()["pass_score"], 80)           # reset itself can be undone

    def test_named_presets_and_gsec_untouched(self):
        self.save(pass_score=72, risk_free=0.071)
        presets.save_preset("Strict")
        self.save(pass_score=60, risk_free=0.069)
        presets.load_preset("Strict")
        st = scoring.settings()
        self.assertEqual(st["pass_score"], 72)
        self.assertEqual(st["risk_free"], 0.069)    # live yield is never rolled back by a preset
        presets.reset()
        self.assertEqual(scoring.settings()["risk_free"], 0.069)


class Signal(unittest.TestCase):
    def test_rules(self):
        s = recommendations.signal
        self.assertEqual(s(True, False, ["buffett"]), "hold")
        self.assertEqual(s(True, True, []), "sell")
        self.assertEqual(s(False, True, ["lynch"]), "buy")
        self.assertEqual(s(False, True, []), "watch")
        self.assertIsNone(s(False, False, ["buffett"]))
        self.assertEqual(s(True, False, ["munger"], overvalued=True), "sell")    # price discipline
        self.assertEqual(s(False, True, ["munger"], overvalued=True), "watch")

    def test_assess_uses_price_discipline(self):
        a = recommendations.assess
        score = {"matches": ["munger"], "valuation": {"multiple": 2.6, "method": "discounted free cash flow"}}
        self.assertEqual(a(True, False, score, {"price_discipline": 2.0})[0], "sell")
        self.assertIn("2.6x", a(True, False, score, {"price_discipline": 2.0})[1])
        self.assertEqual(a(True, False, score, {"price_discipline": 3.0})[0], "hold")
        self.assertEqual(a(True, False, score, {"price_discipline": 0})[0], "hold")            # off
        no_value = {"matches": ["munger"], "valuation": {"multiple": None}}
        self.assertEqual(a(False, True, no_value, {"price_discipline": 2.0})[0], "buy")          # can't judge: not penalised


PAGE = """<h1>Test Co Ltd</h1> NSE: TESTCO
<li><span class="name">Market Cap</span><span class="nowrap value">₹ <span class="number">1,234</span> Cr.</span></li>
<section id="profit-loss"><table><thead><tr><th></th><th>Mar 2023</th><th>Mar 2024</th><th>Mar 2025</th><th>TTM</th></tr></thead>
<tr><td class="text">Sales&nbsp;+</td><td>100</td><td>120</td><td>1,440</td><td>150</td></tr>
<tr><td class="text">Profit before tax</td><td>20</td><td>-10</td><td>30</td><td>31</td></tr>
<tr><td class="text">Tax %</td><td>25%</td><td>-20%</td><td>25%</td><td></td></tr>
<tr><td class="text">Net Profit&nbsp;+</td><td>15</td><td>-8</td><td>22.5</td><td>23</td></tr>
<tr><td class="text">EPS in Rs</td><td>1.5</td><td>-0.8</td><td>2.25</td><td>2.3</td></tr>
<tr><td class="text">Dividend Payout %</td><td>40%</td><td>0%</td><td></td><td></td></tr></table></section>
<section id="balance-sheet"><table><thead><tr><th></th><th>Mar 2024</th><th>Mar 2025</th><th>Sep 2025</th></tr></thead>
<tr><td class="text">Equity Capital</td><td>10</td><td>10</td><td>10</td></tr></table></section>"""


class ScreenerPage(unittest.TestCase):
    def test_parse(self):
        info, items, years = screener_fetch.parse_page(PAGE, "TESTCO")
        self.assertEqual(years, ["Mar 2023", "Mar 2024", "Mar 2025"])             # TTM column ignored
        self.assertEqual((info["name"], info["market_cap"]), ("Test Co Ltd", 1234.0))
        self.assertEqual(info["quarters"]["dates"], [])                             # page has no quarterly section
        self.assertEqual(items["Sales / Revenue"], [100, 120, 1440])
        self.assertEqual(items["Tax"], [5.0, -2.0, 7.5])                          # credit in the loss year
        self.assertEqual(items["Dividend Payout %"], [0.4, 0.0, None])            # blank stays blank
        self.assertEqual(items["Equity Capital"], [None, 10, 10])                 # aligned by date; no Mar 2023 column
        with self.assertRaises(ValueError):
            screener_fetch.parse_page(PAGE, "OTHER")


if __name__ == "__main__":
    unittest.main()
