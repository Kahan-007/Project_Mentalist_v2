import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from india_screener import config, pairs, portfolio, recommendations, scoring


class StrategyRouting(unittest.TestCase):
    def test_fifo_mixed_purposes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config,'DB_PATH',Path(folder)/'ledger.sqlite'):
            portfolio.add('BUY','TCS','2026-01-01',2,100,strategy='pairs',pair='TCS/INFY')
            portfolio.add('BUY','TCS','2026-01-02',3,100,strategy='value')
            score={'symbol':'TCS','matches':['lynch'],'valuation':{'multiple':1.1}}
            with patch.object(pairs,'long_position',return_value=('hold','awaiting convergence')):
                signal, why=recommendations.assess(True,False,score)
                self.assertEqual(signal,'review')
                self.assertIn('2 shares',why)
                self.assertIn('3 shares',why)
            portfolio.add('SELL','TCS','2026-01-03',2,110)
            self.assertEqual(recommendations.assess(True,False,score)[0],'sell')
            with self.assertRaises(portfolio.LedgerError):
                portfolio.add('BUY','TCS','2026-01-04',1,110,strategy='pairs',pair='PFC/RECLTD')
            self.assertEqual(portfolio.holdings()['TCS']['quantity'],3)

    def test_lender_growth_and_payout(self):
        m=dict(roe=[.15]*5,equity=[100],payout=.3,bvps_cagr_5=0)
        st=dict(risk_free=.06,equity_premium=.03)
        self.assertAlmostEqual(scoring.justified_pb_value(m,st)[0],150.)
        self.assertAlmostEqual(scoring.justified_pb_value(dict(m,bvps_cagr_5=.2),dict(st,terminal_growth=.05))[0],200.)
        self.assertIsNone(scoring.justified_pb_value(dict(m,payout=None),st)[0])
        self.assertIsNone(scoring.justified_pb_value(dict(m,roe=[.04]*5,payout=0,bvps_cagr_5=.04),st)[0])

    def test_pair_exit_both_orientations(self):
        model=dict(market_ok=True,z=-1.5,beta=1,t=-4,critical=-3.36,half=8)
        with patch.object(pairs,'inspect',return_value=model):
            self.assertEqual(pairs.long_position('TCS/INFY','TCS')[0],'hold')
            self.assertEqual(pairs.long_position('TCS/INFY','INFY')[0],'sell')
            model['market_ok']=False
            self.assertEqual(pairs.long_position('TCS/INFY','INFY')[0],'review')
