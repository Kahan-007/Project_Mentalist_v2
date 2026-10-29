import copy
import datetime as dt
import json
from unittest import mock
from tests.test_monitor_telegram import Isolated
from tests.helpers import make_series, business_days
from tests.test_data import RSS
from india_screener import config, db as dbm, events, news, research, scoring, tracker


class NewsResearch(Isolated):
    def test_ambiguous_company_names_and_explicit_tickers(self):
        cases = [('OIL','Oil India Limited','Oil prices rise','Oil India announces results'),
                 ('IDEA','Vodafone Idea Limited','A new idea in telecom','Vodafone Idea reports loss'),
                 ('PAGE','Page Industries Limited','A new page for fashion','Page Industries shares rise'),
                 ('STAR','Strides Pharma Science Limited','Star investor buys pharma','Strides Pharma reports results'),
                 ('CARE','CARE Ratings Limited','Health care stocks rise','CARE Ratings announces dividend'),
                 ('RAIN','Rain Industries Limited','RAIN floods factories','Rain Industries issues disclosure')]
        for symbol,name,bad,good in cases:
            terms=news.search_terms(name,symbol)
            self.assertFalse(news._mentions(bad,terms),symbol)
            self.assertTrue(news._mentions(good,terms),symbol)
            self.assertTrue(news._mentions(f'NSE: {symbol} reports results',terms),symbol)
            self.assertTrue(news._mentions(f'{symbol} shares rise',terms),symbol)
        self.assertFalse(news._mentions('OIL prices rise',['OIL']))

    def test_earliest_syndication_and_storage_dedup(self):
        now=dt.datetime(2026,9,24,tzinfo=dt.timezone.utc)
        items=news.parse_rss(RSS.encode(),['Infosys'],now)
        self.assertEqual(items[0]['published_utc'],'2026-09-23T06:00:00+00:00')
        late={**items[0],'published_utc':'2026-09-23T07:00:00+00:00'}
        tracker.store_news('INFY',[late]);tracker.store_news('INFY',items)
        with dbm.connect() as db:
            rows=db.execute('SELECT published_utc FROM news').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],items[0]['published_utc'])

    def test_timezone_and_missing_sessions_do_not_create_event(self):
        stock,bench=make_series(n=160)
        stock.pop(80)
        result=events.assess_dip(stock,bench,bench[140]['date'],self.cfg['event'])
        self.assertEqual(result['status'],'insufficient_data')
        with self.assertRaises(events.InsufficientData):events.event_session('2026-01-01T10:00:00',[])
        sessions=['2026-10-26','2026-10-28']
        self.assertEqual(events.event_session('2026-10-26T15:30:00+05:30',sessions),'2026-10-28')

    def test_new_dip_suppresses_old_level_alert_atomically(self):
        with dbm.connect() as db:
            db.execute("INSERT INTO events(symbol,event_session,status,notified_dip) VALUES('TEST','2026-01-01','dip_confirmed',1)")
            db.execute("INSERT INTO events(symbol,event_session,status) VALUES('TEST','2026-01-02','provisional')")
        with mock.patch.object(events,'assess_dip',return_value={'status':'dip_confirmed'}), \
             mock.patch.object(events,'assess_levelling',return_value={'levelled':False}), \
             mock.patch.object(tracker,'queue_event') as queue:
            result=tracker.evaluate('TEST',[],[],self.cfg)
        self.assertEqual([r['session'] for r in result],['2026-01-02'])
        self.assertEqual(queue.call_count,1)
        self.assertEqual(next(r for r in tracker.list_events() if r['event_session']=='2026-01-01')['status'],'superseded')

    def test_research_costs_duplicate_prices_and_no_future_sessions(self):
        stock=[{'date':f'2026-01-{i:02d}','adjclose':v} for i,v in enumerate([1000,100,90,120],1)]
        bench=[{'date':r['date'],'adjclose':100} for r in stock]
        result=research.outcome('2026-01-01T20:00:00+05:30',stock,bench,2,cost_bps=100)
        self.assertAlmostEqual(result['net_stock_return'],.176237623762)
        self.assertAlmostEqual(result['max_drawdown'],-.1)
        self.assertEqual(research.outcome('2026-01-01T20:00:00+05:30',stock+[stock[-1]],bench,2)['status'],'duplicate price sessions')
        with mock.patch.object(config,'now_ist',return_value=dt.datetime(2026,1,4,14,tzinfo=config.IST)):
            self.assertEqual(research.outcome('2026-01-01T20:00:00+05:30',stock,bench,2)['status'],'awaiting horizon')

    def test_overlap_summary_and_immutable_benchmark(self):
        item={'symbol':'TEST','held':False,'shortlisted':True}
        analysis={'matches':['munger'],'valuation':{'multiple':.5},'algorithm_version':'v1'}
        record={'meta':{'price':100,'price_date':'2026-01-01'}}
        research.capture(item,record,analysis,scoring.DEFAULT_SETTINGS,benchmark='^NSEI')
        analysis['algorithm_version']='v2'
        research.capture(item,record,analysis,scoring.DEFAULT_SETTINGS,benchmark='^OTHER')
        with dbm.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM research_context ORDER BY observation_id')]
        self.assertEqual([(r['benchmark'],r['model_version']) for r in rows],[('^NSEI','v1'),('^OTHER','v2')])
        samples=[]
        for i,entry in enumerate(['2026-01-02','2026-01-03','2026-03-03']):
            samples.append({'id':i,'symbol':'TEST','model_version':'v1','signal':'buy','origin':'confirmed',
                            'outcomes':{'20':{'status':'observed','entry_date':entry,'end_date':'2026-02-02' if i<2 else '2026-04-01','net_excess_return':.1}}})
        result=research.summarize(samples,20)
        self.assertEqual(result['all_overlapping_observations']['samples'],3)
        self.assertEqual(result['nonoverlapping_per_stock']['samples'],2)
        self.assertIn('insufficient evidence',result['evidence'])
