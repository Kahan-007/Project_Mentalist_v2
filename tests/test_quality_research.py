import copy
import datetime as dt
import json
import time
import unittest
from unittest import mock

from india_screener import config, db as dbm, fundamentals, materiality, monitor, outbox, quality, research, risk, scoring, recommendations, server, telegram
from tests.test_monitor_telegram import Isolated
from tests import fixtures_fund as fx


class Triage(unittest.TestCase):
    def test_negation_and_routine(self):
        self.assertEqual(materiality.assess({'title':'Company reports fraud','categories':[]})['label'], 'urgent review')
        self.assertEqual(materiality.assess({'title':'Company denies fraud allegation','categories':[]})['label'], 'investment review')
        self.assertEqual(materiality.assess({'title':'Closure of trading window','categories':[]})['label'], 'routine filing')
        self.assertEqual(materiality.assess({'title':'Default settings updated','categories':[]})['verified_impact'], False)

    def test_order_size_context_not_earnings(self):
        record = {'years':[config.now_ist().date().isoformat()], 'series':{'sales':[1000]}}
        result = materiality.assess({'title':'Company wins Rs. 200 crore order','categories':['deal/order']},record)
        self.assertIn('20.0%',result['context'])
        self.assertIn('not an earnings forecast',result['context'])
        for headline in ('Company wins $200 million order','Company order: Rs 200 cr versus Rs 100 cr last year'):
            self.assertIsNone(materiality.assess({'title':headline},record)['context'])
        record['years'] = ['2000-03-31']
        self.assertIsNone(materiality.assess({'title':'Company wins ₹200 crore order'},record)['context'])


class Audit(Isolated):
    ITEM = {'symbol':'TEST', 'held':True, 'shortlisted':False}

    def head(self, age, title='Company results'):
        return {'title':title,'published_utc':(config.now_ist()-dt.timedelta(seconds=age)).isoformat(),
                'url':'https://example.com/report','source':'test','categories':['results']}

    def test_latency_feedback_and_deduplication(self):
        cfg=copy.deepcopy(self.cfg);cfg['notify'].update(telegram_bot_token='123:'+'x'*30,telegram_chat_id='7')
        h=self.head(60)
        monitor.ingest(self.ITEM,[h],cfg)
        monitor.ingest(self.ITEM,[h],cfg)
        with mock.patch('india_screener.notify.deliver'):
            outbox.flush(cfg)
        report=quality.report()
        self.assertEqual(report['counts'], {'queued':1})
        self.assertEqual(report['queue_to_delivery']['samples'],1)
        self.assertGreaterEqual(report['publication_to_detection']['median_seconds'],60)
        aid=report['recent'][0]['alert_id']
        quality.feedback(aid,'useful');quality.feedback(aid,'noise')
        self.assertEqual(quality.report()['feedback'],{'noise':1})
        with self.assertRaises(ValueError):quality.feedback(aid,'anything')
        with self.assertRaises(ValueError):quality.feedback(999,'useful')
        with self.assertRaises(ValueError):quality.feedback(aid,'useful','x'*1001)

    def test_stale_invalid_and_future_are_visible(self):
        heads=[self.head(8000,'old'),self.head(-3600,'future'),self.head(1,'invalid')]
        heads[-1]['published_utc']='broken'
        self.assertEqual(monitor.ingest(self.ITEM,heads,self.cfg),0)
        self.assertEqual(quality.report()['counts'],{'stale':1,'future':1,'invalid timestamp':1})
        heads[1]['published_utc']=self.head(5)['published_utc']
        self.assertEqual(monitor.ingest(self.ITEM,[heads[1]],self.cfg),1)
        self.assertEqual(quality.report()['counts'].get('queued'),1)

    def test_pipeline_failure_rolls_back_news_and_audit(self):
        with mock.patch.object(outbox,'enqueue',side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):monitor.ingest(self.ITEM,[self.head(1)],self.cfg)
        self.assertEqual(quality.report()['counts'],{})
        with dbm.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM news').fetchone()[0],0)

    def test_source_error_overrides_recent_heartbeat(self):
        state={'news':{'checked_at':config.now_ist().isoformat(),'errors':{'TEST':'FetchError'}}}
        s=quality.source_health(state)
        self.assertEqual(s[0]['state'],'error')
        self.assertEqual(s[1]['state'],'not checked')
        state['news']={'checked_at':(config.now_ist()-dt.timedelta(minutes=10)).isoformat()}
        self.assertEqual(quality.source_health(state)[0]['state'],'stale')

    def test_distribution(self):
        self.assertEqual(quality.distribution([])['median_seconds'],None)
        d=quality.distribution([1,2,3,4,-5,None])
        self.assertEqual(d,{'samples':4,'median_seconds':2.5,'p95_seconds':4})

    def test_bad_bot_feedback_returns_help_not_poison_update(self):
        self.assertIn('Use /feedback',telegram.command('/feedback nope'))
        self.assertIn('Use /feedback',telegram.command('/feedback 999 useful'))


class PortfolioRisk(Isolated):
    def test_weights_and_missing_price_disable_limit_flags(self):
        positions={'ONE':{'quantity':3},'TWO':{'quantity':1}}
        records={s:{'meta':{'price':100,'price_date':config.now_ist().date().isoformat()},'industry':'Technology'} for s in positions}
        cfg=copy.deepcopy(self.cfg);cfg['risk']={'max_stock_weight':.5,'max_sector_weight':.5}
        with mock.patch('india_screener.portfolio.holdings',return_value=positions),mock.patch.object(fundamentals,'load',side_effect=lambda s:records[s]),mock.patch.object(config,'load_config',return_value=cfg):
            report=risk.report()
            self.assertTrue(report['complete_prices'])
            self.assertEqual(report['holdings'][0]['weight'],.75)
            self.assertTrue(report['holdings'][0]['over_limit'])
            self.assertTrue(report['industries'][0]['over_limit'])
            records['TWO']['meta']['price']=None
            report=risk.report()
            self.assertFalse(report['complete_prices'])
            self.assertFalse(report['holdings'][0]['over_limit'])


class Research(Isolated):
    def test_immutable_observations_and_changed_inputs(self):
        item={'symbol':'TEST','held':True,'shortlisted':False}
        rec={'symbol':'TEST','meta':{'price':100}}
        analysis={'matches':['munger'],'valuation':{'multiple':1},'algorithm_version':'test'}
        st=scoring.settings()
        research.capture(item,rec,analysis,st);research.capture(item,rec,analysis,st)
        rec['meta']['price']=200
        research.capture(item,rec,analysis,st)
        with dbm.connect() as db:
            rows=db.execute('SELECT financials FROM research_observation ORDER BY id').fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual(json.loads(rows[0][0])['meta']['price'],100)

    def test_outcome_starts_next_day_and_uses_same_adjusted_series(self):
        stock=[{'date':f'2026-01-{i:02d}','adjclose':v} for i,v in enumerate([1000,100,90,120],1)]
        bench=[{'date':r['date'],'adjclose':100} for r in stock]
        r=research.outcome('2026-01-01T12:00:00+05:30',stock,bench,2)
        self.assertEqual(r['entry_date'],'2026-01-02')
        self.assertAlmostEqual(r['stock_return'],.2)
        self.assertAlmostEqual(r['max_drawdown'],-.1)
        self.assertAlmostEqual(r['excess_return'],.2)
        self.assertEqual(research.outcome('2026-01-01T12:00:00+05:30',stock,bench,20)['status'],'awaiting horizon')
        self.assertEqual(research.outcome('2026-01-01T12:00:00+05:30',[stock[0],stock[1],stock[3]],bench,2)['status'],'incomplete stock history')
        self.assertEqual(research.outcome('2026-01-01T12:00:00+05:30',stock,bench[1:],2)['status'],'baseline history unavailable')


class ValuationControls(Isolated):
    def record(self):
        r=fundamentals.parse_grid(fx.to_grid(fx.quality_company()))
        r.update(symbol='TEST',name='Test',sector_type='general',basis='consolidated')
        return r

    def test_entry_discount_does_not_force_exit(self):
        score={'matches':['munger'],'valuation':{'multiple':.9}}
        st={'price_discipline':2,'entry_margin_of_safety':.25}
        self.assertEqual(recommendations.assess(False,True,score,st)[0],'watch')
        self.assertEqual(recommendations.assess(True,True,score,st)[0],'hold')
        score['valuation']['multiple']=.75
        self.assertEqual(recommendations.assess(False,True,score,st)[0],'buy')
        score['valuation']['multiple']=.9
        st['entry_margin_of_safety']=-1
        self.assertEqual(recommendations.assess(False,True,score,st)[0],'buy')

    def test_sensitivity_keeps_input_settings(self):
        st=scoring.settings();before=copy.deepcopy(st)
        r=scoring.sensitivity(self.record(),st)
        vals=[s['value_cr'] for s in r['scenarios']]
        self.assertLess(vals[0],vals[1]);self.assertLess(vals[1],vals[2])
        self.assertEqual(st,before)

    def test_future_quarter_gate_and_headroom_no_name_error(self):
        r=self.record()
        r['quarters']={'dates':['2099-03-31'],'net_profit':[10]}
        score=scoring.score_company(r)
        self.assertTrue(any('future' in issue for issue in score['data_quality']['issues']))
        scoring.price_headroom(r) # regression: date checks previously referenced undefined locals here
        st={**scoring.settings(),'entry_margin_of_safety':.25}
        self.assertIsNone(scoring.price_headroom(r,st,entry=True))

    def test_settings_and_risk_limits_reject_nonfinite_values(self):
        for key,value in [('entry_margin_of_safety',.9),('pass_score',101),('min_coverage',float('nan'))]:
            with self.assertRaises(ValueError):server.api_settings_save({},json.dumps({'scoring':{key:value}}).encode())
        with self.assertRaises(ValueError):server.api_risk_limits({},b'{"max_stock_weight":1.1,"max_sector_weight":0}')
        server.api_risk_limits({},b'{"max_stock_weight":0.25,"max_sector_weight":0.5}')
        self.assertEqual(config.load_config()['risk']['max_stock_weight'],.25)

    def test_consistent_fcff_and_fcfe_worksheet(self):
        from india_screener.valuation import calculate
        inputs={'kind':'FCFF','cash_flow_cr':100,'discount_rate':.1,'growth':0,'terminal_growth':0,
                'shares_cr':10,'debt_cr':200,'cash_cr':50}
        r=calculate(inputs)
        self.assertAlmostEqual(r['equity_value_cr'],850)
        self.assertAlmostEqual(r['value_per_share'],85)
        self.assertEqual(r['discount_basis'],'WACC')
        r=calculate({**inputs,'kind':'FCFE'})
        self.assertAlmostEqual(r['equity_value_cr'],1000)
        self.assertEqual(r['discount_basis'],'cost of equity')
        for overrides in ({'terminal_growth':.1},{'shares_cr':0},{'debt_cr':-1},{'cash_flow_cr':float('inf')}):
            with self.assertRaises(ValueError):calculate({**inputs,**overrides})

    def test_old_presets_restore_legacy_entry_rule(self):
        from india_screener import presets
        server.api_settings_save({},b'{"scoring":{"entry_margin_of_safety":0.25}}')
        presets._apply({'pass_score':65})
        self.assertEqual(scoring.settings()['entry_margin_of_safety'],-1)

    def test_quality_api_is_local_read_only_and_json_safe(self):
        with mock.patch('india_screener.portfolio.holdings',return_value={}):
            result=server.api_quality({},b'')
        json.dumps(result,allow_nan=False)
        self.assertEqual(result['research']['total'],0)
        self.assertIn('sources',result['monitor'])


if __name__=='__main__':unittest.main()
