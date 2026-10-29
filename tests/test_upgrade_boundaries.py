import datetime as dt
import gzip
import io
import json
import unittest
from unittest import mock
from urllib.error import HTTPError
from india_screener import config, net, news_alerts, risk, server, xlsx


class InputBoundaries(unittest.TestCase):
    def test_bounded_gzip_and_retry_after(self):
        response=mock.MagicMock()
        response.__enter__.return_value=response
        response.headers={'Content-Encoding':'gzip'}
        response.read.return_value=gzip.compress(b'x'*1000)
        with mock.patch.object(net,'MAX_BYTES',100), mock.patch.object(net,'urlopen',return_value=response):
            with self.assertRaisesRegex(net.FetchError,'decompressed response too large'):
                net.fetch('https://example.test/data',retries=0)
        error=HTTPError('https://example.test/token/secret',429,'limited',{'Retry-After':'NaN'},None)
        with mock.patch.object(net,'urlopen',side_effect=error), mock.patch.object(net,'_set_backoff') as backoff:
            with self.assertRaises(net.FetchError) as raised:
                net.fetch('https://example.test/token/secret',retries=0)
        self.assertEqual(backoff.call_args.args,('example.test',60))
        self.assertNotIn('secret',str(raised.exception))
        self.assertTrue(error.closed)

    def test_sparse_xlsx_bounds_and_formula_errors(self):
        def sheet(body):return f'<worksheet xmlns="{xlsx.NS["m"]}"><sheetData>{body}</sheetData></worksheet>'.encode()
        result=xlsx._read_sheet(sheet('<row><c><v>7</v></c><c t="e"><v>#DIV/0!</v></c></row>'),[])
        self.assertEqual(result,[[7,None]])
        for row in ('<row r="2000001"><c r="A2000001"><v>1</v></c></row>',
                    '<row r="1000"><c r="ZZZ1000"><v>1</v></c></row>',
                    '<row><c r="!!!"><v>1</v></c></row>'):
            with self.assertRaises(ValueError):xlsx._read_sheet(sheet(row),[])

    def test_correction_api_rejects_database_path_injection(self):
        with mock.patch.object(server.portfolio,'correct') as correct:
            with self.assertRaises(ValueError):
                server.api_txn_correct({},json.dumps({'id':1,'changes':{'path':'/tmp/other.sqlite'}}))
        correct.assert_not_called()

    def test_research_edit_does_not_bypass_quote_confirmation(self):
        item={'held':False,'shortlisted':True}
        record={'symbol':'TEST','meta':{},'series':{'sales':[100]},'imported_at':'old'}
        a=news_alerts.evidence(item,record,{})
        record.update(valuation_research={'cash_flow_cr':1},imported_at='new',refresh_provenance={'date':'new'})
        b=news_alerts.evidence(item,record,{})
        self.assertEqual(a,b)


class HistoricalRisk(unittest.TestCase):
    def test_stable_holdings_metrics_and_missing_history(self):
        from india_screener import research
        dates=[(dt.date(2026,8,1)+dt.timedelta(days=i)).isoformat() for i in range(21)]
        stock=[{'date':d,'close':100+i,'adjclose':100+i} for i,d in enumerate(dates)]
        bench=[{'date':d,'close':200,'adjclose':200} for d in dates]
        def cached(symbol,*a):return (bench if symbol=='BENCH' else stock),None
        positions={'TEST':{'quantity':2,'exchange':'NSE'}}
        txns=[{'trade_date':'2026-07-31'}]
        with mock.patch.object(research,'cached',side_effect=cached):
            result=risk.stable_holdings_history(positions,{'benchmark':'BENCH'},txns)
            self.assertEqual(result['status'],'observed')
            self.assertAlmostEqual(result['equity_return'],.2)
            self.assertAlmostEqual(result['excess_return'],.2)
            self.assertEqual(result['max_drawdown'],0)
            self.assertGreater(result['annualized_volatility'],0)
            stock.pop(5)
            self.assertEqual(risk.stable_holdings_history(positions,{'benchmark':'BENCH'},txns)['status'],'unavailable')
