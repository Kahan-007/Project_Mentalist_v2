"""Decision alerts with real scoring, timestamp gates, rollback and delivery-policy integration."""
import copy
import datetime as dt
import json
from unittest import mock

from india_screener import (config, db as dbm, fundamentals, monitor, news_alerts, outbox,
                            prices, quality, recommendations, scoring, server, storage, telegram, tracker)
from tests import fixtures_fund as fx
from tests.test_monitor_telegram import Isolated

NOW = dt.datetime(2026, 10, 14, 11, tzinfo=config.IST)


class NewsAlerts(Isolated):
    ITEM = {'symbol':'TEST', 'name':'Test Co', 'aliases':[], 'exchange':'NSE', 'held':False, 'shortlisted':True}

    def setUp(self):
        super().setUp()
        self.cfg['notify'].update(telegram_bot_token='123:'+'x'*30, telegram_chat_id='7')
        p = mock.patch.object(config, 'now_ist', return_value=NOW)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(quality.time, 'time', return_value=NOW.timestamp())
        p.start(); self.addCleanup(p.stop)
        self.st = {**scoring.DEFAULT_SETTINGS, **self.cfg.get('scoring', {})}
        self.rec = fundamentals.parse_grid(fx.to_grid(fx.quality_company(
            years=[f'{y}-03-31' for y in range(2017, 2027)])))
        self.rec.update(symbol='TEST', name='Test Co', basis='consolidated', sector_type='general')
        self.rec['quarters'] = {}
        self.rec['meta'].update(price_date=NOW.date().isoformat(), market_cap_date=NOW.date().isoformat())
        self.value = scoring.score_company(self.rec, self.st)['valuation']['value_cr']/self.rec['series']['shares_cr'][-1]
        self.assertGreater(self.value, 0)

    def head(self, title='Test Co announces quarterly results', age=60, source='test', categories=None):
        return {'title':title, 'published_utc':(NOW-dt.timedelta(seconds=age)).isoformat(),
                'url':'https://example.com/'+title.replace(' ', '-'), 'source':source,
                'categories':categories if categories is not None else ['results']}

    def observe(self, multiple, seconds=0, item=None, rec=None, indicative=True):
        it = item or self.ITEM
        quote = {'symbol':'TEST', 'price':multiple*self.value, 'time':(NOW-dt.timedelta(seconds=seconds)).isoformat()}
        observed = news_alerts.quote_record(rec or self.rec, quote, self.cfg)
        analysis = scoring.score_company(observed, self.st)
        recommendations.log_signal_changes([it], {'TEST':(analysis,None)}, cfg=self.cfg, records={'TEST':observed},
                                           st=self.st, indicative=indicative)
        return observed, analysis

    def pushes(self):
        with dbm.connect() as db:
            return [dict(r) for r in db.execute("SELECT a.* FROM alert_outbox a JOIN alert_delivery d ON d.alert_id=a.id WHERE d.channel='telegram'")]

    def state(self):
        with dbm.connect() as db:
            row = db.execute("SELECT signal FROM signal_state WHERE symbol='TEST'").fetchone()
            return row['signal'] if row else None

    def test_default_filters_routine_and_material_news_without_losing_archive(self):
        heads = [self.head(), self.head('Closure of trading window', categories=[])]
        self.assertEqual(monitor.ingest(self.ITEM, heads, self.cfg), 0)
        self.assertEqual(monitor.ingest(self.ITEM, heads, self.cfg), 0)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(quality.report()['counts'], {'filtered':2})
        self.assertEqual(len(recommendations._recent_headlines('TEST')), 2)

    def test_risk_exception_requires_holding_and_strict_mode_disables_it(self):
        urgent = self.head('Test Co announces debt default', categories=[])
        self.assertEqual(monitor.ingest(self.ITEM, [urgent], self.cfg), 0)
        held = {**self.ITEM, 'symbol':'HELD', 'held':True}
        self.assertEqual(monitor.ingest(held, [urgent], self.cfg), 1)
        body = self.pushes()[0]['body']
        self.assertIn('does not create a Buy/Sell', body)
        denied = self.head('Test Co denies fraud allegations', categories=[])
        self.assertEqual(monitor.ingest(held, [denied], self.cfg), 0)
        self.cfg['news_alerts']['mode'] = 'signals_only'
        self.assertEqual(monitor.ingest({**held, 'symbol':'STRICT'}, [urgent], self.cfg), 0)
        self.assertEqual(len(self.pushes()), 1)

    def test_generic_default_and_fraud_prevention_news_do_not_trigger_holding_risk_pushes(self):
        held = {**self.ITEM, 'held':True}
        heads = [self.head('Test Co updates default settings', categories=[]),
                 self.head('Test Co launches fraud prevention software', categories=[])]
        self.assertEqual(monitor.ingest(held, heads, self.cfg), 0)
        self.assertEqual(self.pushes(), [])

    def test_price_crossing_combines_old_news_reason_boundary_and_frozen_snapshot(self):
        self.observe(2.2, seconds=180)
        self.assertEqual(self.state(), 'watch')
        self.assertEqual(self.pushes(), [])
        # Older than the direct-headline push cutoff, still valid context for a later price move.
        head = self.head(age=7200)
        monitor.ingest(self.ITEM, [head], self.cfg)
        self.observe(1.0, seconds=120)
        self.assertEqual(self.state(), 'watch')
        self.observe(1.0, seconds=120)  # same cached quote cannot be a second confirmation
        self.assertEqual(self.pushes(), [])
        self.observe(1.0, seconds=60)
        self.assertEqual(self.state(), 'buy')
        alerts = self.pushes()
        self.assertEqual(len(alerts), 1)
        self.assertIn('WATCH → BUY', alerts[0]['body'])
        self.assertIn(head['title'], alerts[0]['body'])
        self.assertIn('causation unproven', alerts[0]['body'])
        self.assertIn('Crossed boundary:', alerts[0]['body'])
        row = recommendations.history(full=True)[0]
        self.assertEqual(row['headlines'][0]['title'], head['title'])
        threshold = row['analysis']['transition']['threshold_price']
        self.assertGreater(threshold, self.value)
        self.assertLess(threshold, self.value*2.2)
        self.assertEqual(row['price'], self.value)
        self.assertEqual(row['analysis']['alert_policy']['confirmations'], 2)
        aid = alerts[0]['id']
        quality.feedback(aid, 'useful')
        self.assertEqual(quality.report()['feedback'], {'useful':1})
        self.observe(.9)
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(recommendations.history(full=True)[0], row)

    def test_flicker_resets_confirmation_and_withdrawn_buy_is_pushed(self):
        self.observe(2.2, seconds=240)
        self.observe(1, seconds=180)
        self.observe(2.2, seconds=120)
        self.observe(1, seconds=60)
        self.assertEqual(self.pushes(), [])
        self.observe(1)
        self.assertEqual(len(self.pushes()), 1)
        # A daily completed observation does not need intraday quote confirmation.
        self.observe(2.2, indicative=False)
        self.assertEqual(len(self.pushes()), 2)
        self.assertIn('BUY withdrawn', self.pushes()[1]['title'])

    def test_held_sell_and_entry_margin_use_separate_thresholds(self):
        self.st['entry_margin_of_safety'] = .25
        self.observe(.9, seconds=180)
        self.assertEqual(self.state(), 'watch')
        self.observe(.7, seconds=120)
        self.observe(.7, seconds=60)
        self.assertEqual(self.state(), 'buy')
        threshold = recommendations.history(full=True)[0]['analysis']['transition']['threshold_price']
        self.assertAlmostEqual(threshold/self.value, .75, places=4)
        held = {**self.ITEM, 'held':True}
        self.observe(.9, item=held)
        self.assertEqual(self.state(), 'hold')
        self.observe(2.2, item=held, indicative=False)
        self.assertEqual(self.state(), 'sell')

    def test_settings_change_is_not_labelled_as_news_caused_price_crossing(self):
        self.observe(1, seconds=60)
        self.st['entry_margin_of_safety'] = .25
        # An input change can be assessed using the same quote; it is not a price confirmation.
        self.observe(1, seconds=60)
        self.assertEqual(self.state(), 'watch')
        row = recommendations.history(full=True)[0]
        self.assertIsNone(row['analysis']['transition']['threshold_price'])
        self.assertEqual(row['analysis']['transition']['cause'], 'inputs or data availability changed')

    def test_news_after_quote_future_invalid_and_outside_window_are_excluded(self):
        self.observe(2.2, seconds=180)
        heads = [self.head(age=10), self.head('Very old results', age=73*3600),
                 self.head('Future results', age=-3600), self.head('Invalid results')]
        heads[-1]['published_utc'] = 'invalid'
        monitor.ingest(self.ITEM, heads, self.cfg)
        self.observe(1, seconds=120)
        self.observe(1, seconds=60)
        self.assertEqual(recommendations.history(full=True)[0]['headlines'], [])
        self.assertIn('not attributed to a headline', self.pushes()[0]['body'])

    def test_routine_excluded_exchange_disclosure_prioritized_feedback_not_double_counted(self):
        self.observe(2.2, seconds=180)
        heads = [self.head('Closure of trading window', age=300, categories=[]), self.head(age=300),
                 self.head('Test Co quarterly results disclosure', age=600, source='NSE corporate announcement')]
        monitor.ingest(self.ITEM, heads, self.cfg)
        self.observe(1, seconds=120)
        self.observe(1, seconds=60)
        linked = recommendations.history(full=True)[0]['headlines']
        self.assertEqual(len(linked), 2)
        self.assertEqual(linked[0]['source'], 'NSE corporate announcement')
        quality.feedback(self.pushes()[0]['id'], 'noise')
        self.assertEqual(quality.report()['feedback'], {'noise':1})

    def test_failed_queue_rolls_back_confirmation_signal_ledger_and_news_link(self):
        self.observe(2.2, seconds=180)
        monitor.ingest(self.ITEM, [self.head(age=300)], self.cfg)
        self.observe(1, seconds=120)
        with mock.patch.object(outbox, 'enqueue', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                self.observe(1, seconds=60)
        self.assertEqual(self.state(), 'watch')
        self.assertEqual(len(recommendations.history()), 1)
        self.assertEqual(quality.report()['counts'], {'filtered':1})
        self.observe(1, seconds=60)
        self.assertEqual(len(self.pushes()), 1)

    def test_stale_financials_and_undefined_valuation_never_create_buy(self):
        self.observe(2.2, seconds=60)
        rec = copy.deepcopy(self.rec)
        rec['years'] = [f'{y}-03-31' for y in range(2000, 2010)]
        self.observe(.5, rec=rec)
        self.assertEqual(self.state(), 'review')
        self.assertEqual(self.pushes(), [])
        analysis = {'matches':['munger'], 'valuation':{'multiple':None}}
        recommendations.log_signal_changes([self.ITEM], {'TEST':(analysis,None)}, cfg=self.cfg)
        self.assertNotEqual(self.state(), 'buy')

    def test_quote_validation_and_ephemeral_repricing(self):
        original = copy.deepcopy(self.rec)
        quote = {'symbol':'TEST', 'price':100, 'time':NOW.isoformat()}
        observed = news_alerts.quote_record(self.rec, quote, self.cfg)
        self.assertEqual(self.rec, original)
        self.assertEqual(observed['series'], original['series'])
        for changed in ({'price':float('nan')}, {'price':float('inf')}, {'price':0}, {'price':True}, {'symbol':'OTHER'},
                        {'time':(NOW+dt.timedelta(seconds=1)).isoformat()},
                        {'time':(NOW-dt.timedelta(minutes=21)).isoformat()}, {'time':'2026-10-14T10:59:00'}):
            with self.assertRaises(ValueError):
                news_alerts.quote_record(self.rec, {**quote, **changed}, self.cfg)
        self.rec['series']['shares_cr'][-1] = 0
        with self.assertRaises(ValueError):
            news_alerts.quote_record(self.rec, quote, self.cfg)

    def test_monitor_refreshes_quotes_and_failed_quote_cannot_change_state(self):
        storage.write_json(config.DATA/'fundamentals'/'TEST.json', self.rec)
        quotes = [{'symbol':'TEST', 'price':f*self.value, 'time':(NOW-dt.timedelta(seconds=s)).isoformat()}
                  for f,s in [(2.2,180),(1,120),(1,60)]]
        with mock.patch.object(config, 'load_config', return_value=self.cfg), \
             mock.patch('india_screener.daily.watchlist', return_value=[self.ITEM]), \
             mock.patch.object(server, 'size_ranks', return_value={}), \
             mock.patch.object(prices, 'quote_now', side_effect=quotes) as fetch:
            self.assertEqual([monitor.scan_signals_once() for _ in range(3)], [0,0,1])
            self.assertEqual(fetch.call_count, 3)
        self.assertEqual(self.state(), 'buy')
        self.assertEqual(fundamentals.load('TEST'), self.rec)
        with mock.patch.object(config, 'load_config', return_value=self.cfg), \
             mock.patch('india_screener.daily.watchlist', return_value=[self.ITEM]), \
             mock.patch.object(server, 'size_ranks', return_value={}), \
             mock.patch.object(prices, 'quote_now', side_effect=prices.DataError('source failed')):
            self.assertEqual(monitor.scan_signals_once(), 0)
        self.assertEqual(self.state(), 'buy')
        with dbm.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_observation').fetchone()[0], 1)
        self.assertIn('confirmed model buy', telegram.command('/stock TEST'))

    def test_event_alerts_stay_local_and_all_mode_restores_push(self):
        event = {'symbol':'TEST', 'session':NOW.date().isoformat(), 'type':'dip', 'detail':{'dip':{}}}
        with dbm.connect() as db:
            tracker.queue_event(event, self.cfg, db)
        self.assertEqual(self.pushes(), [])
        self.cfg['news_alerts']['mode'] = 'all'
        with dbm.connect() as db:
            tracker.queue_event({**event, 'session':'2026-10-13'}, self.cfg, db)
        self.assertEqual(len(self.pushes()), 1)

    def test_settings_validate_and_preserve_other_config(self):
        storage.write_json(config.CONFIG_PATH, {'scoring':{'risk_free':.07}, 'notify':{'windows_toast':False}})
        result = server.api_settings_save({}, json.dumps({'news_alerts':{'mode':'signals_only', 'confirmations':'3'}}).encode())
        self.assertEqual(result['news_alerts']['confirmations'], 3)
        self.assertEqual(config.load_config()['scoring']['risk_free'], .07)
        self.assertEqual(config.load_config()['news_alerts']['mode'], 'signals_only')
        for values in ({'mode':'bogus'}, {'confirmations':1.5}, {'confirmations':0},
                       {'correlation_hours':169}, {'quote_max_age_minutes':float('nan')}):
            with self.assertRaises(ValueError):
                server.api_settings_save({}, json.dumps({'news_alerts':values}).encode())
        self.assertIn('signals_only', telegram.command('/alerts'))

    def test_dry_run_does_not_consume_signal_confirmation(self):
        recommendations.log_signal_changes([self.ITEM], {'TEST':(None,None)}, publish=False, cfg=self.cfg)
        self.assertIsNone(self.state())
        with dbm.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM signal_evidence').fetchone()[0], 0)

    def test_tightened_policy_cancels_pending_headline_push_but_keeps_commands_and_log(self):
        self.cfg['news_alerts']['mode'] = 'all'
        monitor.ingest(self.ITEM, [self.head()], self.cfg)
        outbox.enqueue('Command reply', '/help', 'telegram:reply:42', self.cfg)
        self.cfg['news_alerts']['mode'] = 'actionable'
        self.assertEqual(news_alerts.apply_pending_filter(self.cfg), 1)
        with dbm.connect() as db:
            statuses = {(r['title'],r['channel']):r['status'] for r in db.execute(
                'SELECT a.title,d.channel,d.status FROM alert_outbox a JOIN alert_delivery d ON d.alert_id=a.id')}
        self.assertEqual(statuses[('TEST: investment review','telegram')], 'cancelled')
        self.assertEqual(statuses[('TEST: investment review','log')], 'pending')
        self.assertEqual(statuses[('Command reply','telegram')], 'pending')

    def test_combined_risk_news_feedback_works_even_if_direct_risk_alert_already_sent(self):
        held = {**self.ITEM, 'held':True}
        self.observe(1, seconds=180, item=held)
        monitor.ingest(held, [self.head('Test Co announces debt default', age=300, categories=[])], self.cfg)
        self.observe(2.2, seconds=120, item=held)
        self.observe(2.2, seconds=60, item=held)
        self.assertEqual(len(self.pushes()), 2)
        quality.feedback(self.pushes()[1]['id'], 'useful')
        self.assertEqual(quality.report()['feedback'], {'useful':1})

    def test_indicative_reader_rejects_wrong_instrument_currency_and_nonfinite_values(self):
        meta = {'symbol':'TEST.NS', 'currency':'INR', 'regularMarketPrice':100,
                'chartPreviousClose':101, 'regularMarketTime':NOW.timestamp()}
        for changed in ({'symbol':'OTHER.NS'}, {'currency':'USD'}, {'regularMarketPrice':float('inf')},
                        {'regularMarketTime':float('nan')}):
            payload = json.dumps({'chart':{'result':[{'meta':{**meta, **changed}}]}}).encode()
            with mock.patch('india_screener.net.fetch', return_value=payload):
                with self.assertRaises(prices.DataError):
                    prices.quote_now('TEST')

    def test_failed_daily_refresh_cannot_revert_newer_live_signal_using_old_saved_price(self):
        self.observe(1, seconds=60)
        older = copy.deepcopy(self.rec)
        older['meta'].update(price=self.value*2.2, market_cap=self.value*2.2*older['series']['shares_cr'][-1],
                             price_date='2026-10-13', market_cap_date='2026-10-13')
        analysis = scoring.score_company(older, self.st)
        self.assertEqual(recommendations.assess(False, True, analysis, self.st)[0], 'watch')
        recommendations.log_signal_changes([self.ITEM], {'TEST':(analysis,None)}, cfg=self.cfg,
                                           records={'TEST':older}, st=self.st)
        self.assertEqual(self.state(), 'buy')
        self.assertEqual(self.pushes(), [])
        with dbm.connect() as db:
            evidence = json.loads(db.execute("SELECT snapshot FROM signal_evidence WHERE symbol='TEST'").fetchone()[0])
        self.assertEqual(evidence['price_snapshot']['price_date'], '2026-10-14')
