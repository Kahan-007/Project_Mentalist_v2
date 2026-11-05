import math
import random
import unittest
from datetime import date, timedelta
from india_screener import pairs


def fixture():
    rng = random.Random(27)
    x, spread = 4., 0.
    a,b = [],[]
    for i in range(251):
        x += rng.gauss(0,.017)
        spread = .84*spread + rng.gauss(0,.007)
        day = (date(2024,1,1)+timedelta(days=i)).isoformat()
        a.append(dict(date=day,adjclose=math.exp(.8+1.3*x+spread)))
        b.append(dict(date=day,adjclose=math.exp(x)))
    return a,b


class PairMath(unittest.TestCase):
    def test_holdout_direction_and_guards(self):
        a,b = fixture()
        today=date.fromisoformat(a[-1]['date'])
        fit=pairs.analyze(a,b,today)
        for target,side in [(2.8,'B'),(-2.8,'A'),(4.,None),(0.,None)]:
            log_a=fit['alpha']+fit['beta']*math.log(b[-1]['adjclose'])+fit['mean']+target*fit['sd']
            a[-1]['adjclose']=math.exp(log_a)
            current=pairs.analyze(a,b,today)
            self.assertAlmostEqual(current['z'],target,places=10)
            self.assertEqual(current['direction'],side)
            self.assertEqual(current['t'],fit['t'])
            self.assertEqual(current['beta'],fit['beta'])
            self.assertIsNone(pairs.analyze(a,b,today,False)['direction'])
        with self.assertRaises(ValueError):
            pairs.analyze(a+[a[-1]],b,today)
        with self.assertRaises(ValueError):
            pairs.analyze(a,a,today)

class PairAlert(unittest.TestCase):
    def test_entry_is_queued_once_with_both_legs_and_reason(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder, patch.object(pairs.config,'DB_PATH',Path(folder)/'test.sqlite'):
            key='TCS/INFY'
            pairs.watch(key,True)
            result=dict(dates=['2026-11-04'],direction='A',cheap='TCS',rich='INFY',z=-2.4,
                        beta=1.1,t=-4.6,critical=-3.36,half=7,matches=['lynch'])
            with patch.object(pairs,'inspect',return_value=result):
                pairs.poll()
                pairs.poll()
                result['dates']=['2026-11-05']
                pairs.poll()
            with pairs.db.connect() as conn:
                rows=conn.execute("SELECT title,body FROM alert_outbox").fetchall()
            self.assertEqual(len(rows),1)
            self.assertIn('buy review TCS / sell review INFY',rows[0][0])
            self.assertIn('Residual ADF=-4.600',rows[0][1])
