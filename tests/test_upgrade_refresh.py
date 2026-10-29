import copy
import datetime as dt
import json
from unittest import mock
from tests.test_notify_refresh import TempData, rec
from india_screener import autofill, config, fundamentals, prices, screener_fetch, storage, workbook


class RefreshIntegrity(TempData):
    def test_partial_schedules_preserve_cells_quarters_and_newer_price(self):
        old = rec(['2024-03-31','2025-03-31'], ['2025-06-30'], source='Screener.in public page old')
        old['series']['cash'] = [100, 200]
        old['quarters']['net_profit'] = [20]
        old['quarters']['interest'] = [2]
        new = copy.deepcopy(old)
        new['series']['cash'] = [None, 210]
        new['quarters'] = {'dates':['2025-06-30'], 'net_profit':[None]}
        new['meta'].update(price=70, price_date='2026-09-26')
        merged, _ = screener_fetch.merge_refresh(new, old)
        self.assertEqual(merged['series']['cash'], [100,210])
        self.assertEqual(merged['quarters'], old['quarters'])
        self.assertEqual(merged['meta']['price'], 70)
        self.assertIn('cash:2024-03-31', merged['refresh_provenance']['retained_cells'])
        new['basis'] = 'standalone'
        with self.assertRaises(ValueError): screener_fetch.merge_refresh(new, old)

    def test_short_refresh_keeps_older_annual_history(self):
        old=rec(['2022-03-31','2023-03-31','2024-03-31','2025-03-31'],source='Screener.in public page old')
        old['series']['net_profit']=[10,20,30,40]
        new=rec(['2024-03-31','2025-03-31'],source='Screener.in public page new')
        new['series']['net_profit']=[31,41]
        merged,_=screener_fetch.merge_refresh(new,old)
        self.assertEqual(merged['years'],old['years'])
        self.assertEqual(merged['series']['net_profit'],[10,20,31,41])
        self.assertEqual(merged['refresh_provenance']['retained_periods'],old['years'][:2])

    def test_explicit_import_can_update_same_year_export(self):
        old=rec(['2024-03-31','2025-03-31'],['2025-06-30'],source='reviewed.xlsx')
        new=copy.deepcopy(old);new['series']['net_profit']=[100,200]
        self.assertIsNone(screener_fetch.merge_refresh(new,old)[0])
        merged,_=screener_fetch.merge_refresh(new,old,preserve_export=False)
        self.assertEqual(merged['series']['net_profit'],[100,200])

    def test_empty_price_result_is_failure(self):
        with mock.patch.object(fundamentals,'all_symbols',return_value=['TEST']), \
             mock.patch.object(fundamentals,'load',return_value={'series':{'price':[None]}}), \
             mock.patch.object(prices,'history',return_value=[]), \
             mock.patch.object(workbook,'fill_prices',return_value=False):
            result=autofill.fill_all_prices()
        self.assertEqual(result['filled'],0)
        self.assertEqual(len(result['failed']),1)

    def test_batch_mixed_and_total_failure_retain_records(self):
        old = rec(['2024-03-31','2025-03-31'])
        for sym in ['ONE','TWO']:
            storage.write_json(config.DATA/'fundamentals'/f'{sym}.json', {**old,'symbol':sym})
        original = fundamentals.load('TWO')
        def fetch(sym, **kw):
            if sym == 'TWO': raise ValueError('provider unavailable')
            return {**copy.deepcopy(old), 'symbol':sym, 'source':'Screener.in public page new'}
        with mock.patch.object(screener_fetch, 'fetch', side_effect=fetch):
            result = screener_fetch.refresh(['ONE','TWO'], pause=0)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(fundamentals.load('TWO'), original)
        with mock.patch.object(screener_fetch, 'fetch', side_effect=ValueError('down')):
            result = screener_fetch.refresh(['ONE','TWO'], pause=0)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(fundamentals.load('TWO'), original)

    def test_failed_jobs_not_marked_success_and_retry_next_day(self):
        stamp = dt.datetime(2026,10,28,20,tzinfo=config.IST)
        path = config.DATA/'state.json'
        with mock.patch.object(autofill,'STATE_PATH',path), mock.patch.object(config,'now_ist',return_value=stamp), \
             mock.patch.object(autofill,'update_gsec',return_value={}), \
             mock.patch.object(autofill,'refresh_financials',return_value={'updated':0,'failed':2,'unchanged':0,'kept':0}), \
             mock.patch.object(autofill,'fill_gnpa',return_value={'filled':[]}), \
             mock.patch.object(autofill,'fill_ownership',return_value={'filled':1,'failed':['TWO']}), \
             mock.patch.object(autofill,'fill_all_prices',return_value={'filled':0,'total':2,'failed':['a','b']}):
            result = autofill.run()
            state = storage.read_json(path)
            self.assertEqual(result['financials']['status'], 'failed')
            self.assertEqual(result['ownership']['status'], 'partial')
            self.assertNotIn('financials', state)
            self.assertEqual(state['jobs']['financials']['next_retry'], '2026-10-29')
            self.assertEqual(state['jobs']['gnpa']['status'], 'success')
            self.assertIn('skipped', autofill.run()['financials'])

    def test_interrupted_job_records_attempt_before_start(self):
        path = config.DATA/'state.json'
        with mock.patch.object(autofill,'STATE_PATH',path), mock.patch.object(autofill,'update_gsec',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): autofill.run()
        self.assertEqual(storage.read_json(path)['jobs']['gsec']['status'], 'running')

    def test_numbers_and_price_date_guard(self):
        self.assertIsNone(fundamentals._num('NaN'))
        self.assertIsNone(fundamentals._num('Infinity'))
        old = rec(['2024-03-31','2025-03-31'])
        old['series']['shares_cr'] = [10,10]
        old['meta'].pop('price_date')
        old['meta']['market_cap_date'] = '2026-09-26'
        storage.write_json(config.DATA/'fundamentals'/'TESTCO.json', old)
        self.assertEqual(workbook.update_current_price('TESTCO',40,'2026-09-25'), 'stale')

class PriceIntegrity(TempData):
    def test_placeholders_duplicates_instrument_and_adjustment(self):
        from tests.test_data import chart
        now = dt.datetime(2026,10,28,20,tzinfo=config.IST)
        payload = chart(['2026-10-20','2026-10-21','2026-10-22'], [100,100,101])
        obj = payload['chart']['result'][0]
        obj['meta'].update(symbol='TEST.NS', instrumentType='EQUITY')
        q = obj['indicators']['quote'][0]
        for key in ('open','high','low','close'): q[key][1] = 100
        q['volume'][1] = 0
        bars, warnings = prices.parse_chart(payload, now, 'TEST.NS')
        self.assertEqual([b['date'] for b in bars], ['2026-10-20','2026-10-22'])
        self.assertIn('placeholder', warnings[0])
        q['volume'][1] = 1
        self.assertEqual(len(prices.parse_chart(payload, now)[0]), 3)
        with self.assertRaises(prices.DataError): prices.parse_chart(payload, now, 'OTHER.NS')
        obj['timestamp'][2] = obj['timestamp'][1]
        with self.assertRaises(prices.DataError): prices.parse_chart(payload, now)
        obj['indicators'].pop('adjclose')
        with self.assertRaises(prices.DataError): prices.parse_chart(payload, now)

    def test_failure_returns_cached_data_with_explicit_status(self):
        cfg = config.load_config()
        bars = [{'date':'2026-09-24','close':100}]
        path = config.CACHE/'prices'/'TEST.NS_2y.json.gz'
        storage.write_json(path, {'bars':bars,'key':'old'})
        with mock.patch.object(prices.net,'fetch',side_effect=prices.net.FetchError('offline')):
            self.assertEqual(prices.history('TEST', cfg=cfg), bars)
        self.assertEqual(prices.history_status('TEST')['state'], 'stale fallback')
