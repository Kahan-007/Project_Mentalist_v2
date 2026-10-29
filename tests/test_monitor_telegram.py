import copy
import datetime as dt
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from india_screener import config, db as dbm, fundamentals, monitor, news, outbox, recommendations, scoring, telegram
from tests import fixtures_fund as fx


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for k, v in {'ROOT': root, 'DATA': root/'data', 'DB_PATH': root/'db.sqlite',
                     'LOGS': root/'logs', 'CONFIG_PATH': root/'config.json', 'SHORTLIST_PATH': root/'shortlist.json'}.items():
            patch = mock.patch.object(config, k, v)
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.dict('os.environ', {'SCREENER_TELEGRAM_TOKEN': '', 'SCREENER_TELEGRAM_CHAT': ''})
        patch.start(); self.addCleanup(patch.stop)
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg['notify']['windows_toast'] = False


class Delivery(Isolated):
    def test_atomic_rollback_and_deduplication(self):
        with self.assertRaises(RuntimeError):
            with dbm.connect() as db:
                outbox.enqueue('A', 'body', 'one', self.cfg, db)
                raise RuntimeError('rollback')
        self.assertEqual(outbox.status(), {})
        outbox.enqueue('A', 'body', 'one', self.cfg)
        outbox.enqueue('A', 'body', 'one', self.cfg)
        self.assertEqual(outbox.status(), {'pending': 1})

    def test_failure_retry_and_no_resend(self):
        outbox.enqueue('A', 'body', 'one', self.cfg)
        with mock.patch('india_screener.notify.deliver', side_effect=telegram.TelegramError('HTTP 429', 60)):
            self.assertEqual(len(outbox.flush(self.cfg)), 1)
        with dbm.connect() as db:
            row = db.execute('SELECT * FROM alert_delivery').fetchone()
            self.assertGreater(row['next_attempt'], time.time()+50)
            db.execute('UPDATE alert_delivery SET next_attempt=0')
        with mock.patch('india_screener.notify.deliver') as send:
            outbox.flush(self.cfg); outbox.flush(self.cfg)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(outbox.status(), {'sent': 1})

    def test_two_workers_claim_once(self):
        outbox.enqueue('A', 'body', 'one', self.cfg)
        errors = []
        def worker():
            try:
                outbox.flush(self.cfg)
            except Exception as exc:
                errors.append(exc)
        with mock.patch('india_screener.notify.deliver') as send:
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(send.call_count, 1)


class Pairing(Isolated):
    TOKEN = '123456:'+'x'*30

    def begin(self):
        with mock.patch.object(telegram, 'call', side_effect=[{'username':'mentalist_test_bot'}, {'url':''}]):
            result = telegram.begin(self.TOKEN)
        self.assertIn('https://t.me/mentalist_test_bot?start=', result['url'])
        self.assertNotIn(self.TOKEN, json.dumps(telegram.status()))
        self.assertEqual(telegram._path().stat().st_mode & 0o777, 0o600)
        return telegram.load()['nonce']

    def update(self, n, text, user=7, kind='private'):
        return {'update_id':n, 'message':{'text':text, 'chat':{'id':user,'type':kind}, 'from':{'id':user,'is_bot':False}}}

    def test_only_nonce_in_private_chat_pairs_and_only_owner_commands(self):
        nonce = self.begin()
        updates = [self.update(0, 'नमस्ते'), self.update(1, '/start wrong'), self.update(2, '/start '+nonce, kind='group'),
                   self.update(3, '/start '+nonce), self.update(4, '/portfolio', user=8), self.update(5, '/help')]
        with mock.patch.object(telegram, 'call', return_value=updates), mock.patch.object(telegram, 'command', return_value='ok') as command:
            telegram.poll_once()
        self.assertTrue(telegram.status()['linked'])
        self.assertEqual(command.call_count, 2)
        self.assertEqual(telegram.load()['offset'], 6)
        with dbm.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM alert_outbox').fetchone()[0], 2)
        with mock.patch.object(telegram, 'call', return_value=[updates[-1]]), mock.patch.object(telegram, 'command', return_value='ok'):
            telegram.poll_once()
        with dbm.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM alert_outbox').fetchone()[0], 2)

    def test_paired_user_can_route_alerts_to_group_without_moving_private_commands(self):
        nonce = self.begin()
        group = {'update_id':2, 'message':{'text':'/setgroup@mentalist_test_bot',
                 'chat':{'id':-10042,'type':'supergroup','title':'spongebob'},
                 'from':{'id':7,'is_bot':False}}}
        updates = [self.update(0, '/setgroup', user=8, kind='supergroup'),
                   self.update(1, '/start '+nonce), group]
        with mock.patch.object(telegram, 'call', return_value=updates):
            telegram.poll_once()

        state = telegram.load()
        self.assertEqual(state['chat_id'], '7')
        self.assertEqual(state['notify_chat_id'], '-10042')
        self.assertEqual(telegram.status()['group_title'], 'spongebob')
        self.assertEqual(config.load_config()['notify']['telegram_chat_id'], '-10042')
        with dbm.connect() as db:
            self.assertEqual(db.execute(
                "SELECT destination FROM alert_delivery WHERE channel='telegram' ORDER BY id DESC"
            ).fetchall()[0]['destination'], '-10042')

        with mock.patch.object(telegram, 'call', return_value=[self.update(3, '/help')]):
            telegram.poll_once()
        with dbm.connect() as db:
            destinations = [row['destination'] for row in db.execute(
                "SELECT destination FROM alert_delivery WHERE channel='telegram' ORDER BY id"
            ).fetchall()]
            self.assertEqual(destinations, ['7', '-10042', '7'])

    def test_privatealerts_restores_private_delivery(self):
        nonce = self.begin()
        with mock.patch.object(telegram, 'call', return_value=[self.update(0, '/start '+nonce)]):
            telegram.poll_once()
        state = telegram.load()
        state.update(notify_chat_id='-10042', group_title='spongebob')
        telegram.save(state)

        with mock.patch.object(telegram, 'call', return_value=[self.update(1, '/privatealerts')]):
            telegram.poll_once()

        self.assertNotIn('notify_chat_id', telegram.load())
        self.assertEqual(config.load_config()['notify']['telegram_chat_id'], '7')
        self.assertFalse(telegram.status()['group_linked'])

    def test_expired_nonce_cannot_pair(self):
        nonce = self.begin()
        s = telegram.load(); s['expires'] = 0; telegram.save(s)
        with mock.patch.object(telegram, 'call', return_value=[self.update(1, '/start '+nonce)]):
            telegram.poll_once()
        self.assertFalse(telegram.status()['linked'])

    def test_disconnect_cancels_pending_and_overrides_legacy(self):
        self.cfg['notify'].update(telegram_bot_token=self.TOKEN, telegram_chat_id='7')
        config.CONFIG_PATH.write_text(json.dumps({'notify':self.cfg['notify']}))
        outbox.enqueue('A', 'body', cfg=self.cfg)
        telegram.disconnect()
        self.assertEqual(outbox.status(), {'cancelled':1, 'pending':1})
        self.assertEqual(config.load_config()['notify']['telegram_bot_token'], '')

    def test_connection_error_never_exposes_token(self):
        with mock.patch.object(telegram, 'urlopen', side_effect=OSError('https://api.telegram.org/bot'+self.TOKEN)):
            with self.assertRaises(telegram.TelegramError) as ctx:
                telegram.call(self.TOKEN, 'getMe')
        self.assertNotIn(self.TOKEN, str(ctx.exception))


class Headlines(Isolated):
    ITEM = {'symbol':'INFY','name':'Infosys Limited','aliases':[],'exchange':'NSE','held':True,'shortlisted':False}

    def test_nse_maps_issuer_and_converts_ist(self):
        xml = b'<rss><channel><item><title>Infosys Limited</title><description>Quarterly results</description><link>https://nsearchives.nseindia.com/corporate/INFY.pdf</link><pubDate>10-Oct-2026 20:07:48</pubDate></item><item><title>Other Ltd</title><description>Infosys acquisition</description><link>https://nsearchives.nseindia.com/corporate/OTHER.pdf</link><pubDate>10-Oct-2026 20:07:48</pubDate></item></channel></rss>'
        heads = news.parse_nse(xml, [self.ITEM])['INFY']
        self.assertEqual(len(heads), 1)
        self.assertEqual(heads[0]['published_utc'], '2026-10-10T14:37:48+00:00')
        self.assertIn('results', heads[0]['categories'])

    def test_fresh_alert_old_storage_duplicates_and_future(self):
        self.cfg['news_alerts']['mode'] = 'all'  # explicitly exercise the legacy unfiltered feed
        now = config.now_ist()
        def head(title, minutes):
            return {'title':title, 'url':'https://example.com/'+title, 'source':'test', 'categories':[],
                    'published_utc': (now-dt.timedelta(minutes=minutes)).isoformat()}
        heads = [head('fresh', 1), head('old', 70), head('future', -5)]
        self.assertEqual(monitor.ingest(self.ITEM, heads, self.cfg), 1)
        self.assertEqual(monitor.ingest(self.ITEM, heads, self.cfg), 0)
        with dbm.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM news').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT count(*) FROM app_notifications').fetchone()[0], 1)


class DataGates(Isolated):
    def record(self):
        r = fundamentals.parse_grid(fx.to_grid(fx.quality_company()))
        r.update(symbol='T', name='T', sector_type='general', basis='consolidated', source='test')
        return r

    def test_missing_analysis_valuation_and_stale_never_buy_sell(self):
        self.assertEqual(recommendations.assess(True, False, None)[0], 'review')
        for score in [ {'matches':['munger'], 'valuation':{'multiple':None}},
                       {'matches':[], 'data_quality':{'issues':['stale']}},
                       {'matches':['munger'], 'provisional':True} ]:
            self.assertEqual(recommendations.assess(True, False, score)[0], 'review')
            self.assertEqual(recommendations.assess(False, True, score)[0], 'review')

    def test_unknown_debt_interest_cash_and_undefined_growth(self):
        r = self.record()
        r['series']['borrowings'][-1] = None
        r['series']['interest'][-1] = None
        r['series']['cash'][-1] = None
        r['series']['net_profit'][0] = -1
        m = fundamentals.metrics(r)
        self.assertIsNone(m['de_ratio'][-1])
        self.assertIsNone(m['interest_cover'])
        self.assertIsNone(m['roic'][-1])
        self.assertIsNone(m['eps_cagr_10'])

    def test_ttm_requires_four_actual_contiguous_quarters(self):
        r = self.record()
        r['quarters'] = {'dates':['2026-03-31','2026-06-30','2026-09-30','2026-12-31'],'net_profit':[]}
        self.assertIsNone(fundamentals.metrics(r)['ttm'])
        r['quarters']['net_profit'] = [10, 11, 12, 13]
        with mock.patch.object(config, 'now_ist', return_value=dt.datetime(2027, 1, 1, tzinfo=config.IST)):
            self.assertEqual(fundamentals.metrics(r)['ttm']['net_profit'], 46)
        r['quarters']['dates'][1] = '2026-03-31'
        self.assertIsNone(fundamentals.metrics(r)['ttm'])

    def test_signal_baseline_change_snapshot_and_dry_run(self):
        it = {'symbol':'T','held':True,'shortlisted':False}
        good = {'matches':['munger'], 'valuation':{'multiple':1}}
        recommendations.log_signal_changes([it], {'T':(good,None)})
        self.assertEqual(outbox.status(), {})
        recommendations.log_signal_changes([it], {'T':(None,None)}, publish=False)
        self.assertEqual(outbox.status(), {})
        recommendations.log_signal_changes([it], {'T':(None,None)})
        recommendations.log_signal_changes([it], {'T':(None,None)})
        self.assertEqual(outbox.status(), {'pending':1})
        rows = recommendations.history(full=True)
        self.assertEqual(len(rows), 2)
        self.assertIn('settings_snapshot', rows[0]['analysis'])

    def test_daily_no_notify_does_not_consume_event_and_enqueue_failure_rolls_back(self):
        from india_screener import tracker
        from tests.helpers import make_series
        stock, bench = make_series(n=303, shocks={300:-0.09}, calm_after=302, seed=11)
        with dbm.connect() as db:
            db.execute("INSERT INTO events(symbol,exchange,event_session,status) VALUES('T','NSE',?,'provisional')", (stock[300]['date'],))
        tracker.evaluate('T', stock, bench, self.cfg, publish=False)
        self.assertEqual(tracker.list_events()[0]['notified_dip'], 0)
        self.assertEqual(outbox.status(), {})
        with mock.patch('india_screener.outbox.enqueue', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                tracker.evaluate('T', stock, bench, self.cfg)
        self.assertEqual(tracker.list_events()[0]['notified_dip'], 0)
        tracker.evaluate('T', stock, bench, self.cfg)
        self.assertEqual(tracker.list_events()[0]['notified_dip'], 1)
        self.assertEqual(outbox.status(), {'pending':1})


class Services(unittest.TestCase):
    def test_launchd_arguments_are_explicit_and_daily_runs_weekdays(self):
        from india_screener import service
        dashboard, daily = service.definitions(8766)
        self.assertEqual(dashboard['ProgramArguments'][-1], '8766')
        self.assertTrue(dashboard['KeepAlive'])
        self.assertEqual([s['Weekday'] for s in daily['StartCalendarInterval']], [1,2,3,4,5])
        self.assertEqual(daily['StartCalendarInterval'][0]['Hour'], 18)


if __name__ == '__main__':
    unittest.main()
