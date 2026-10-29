"""Ledger failures leave no committed transactions or audit entries."""
import sqlite3
import unittest
from decimal import Decimal
from unittest import mock
from tests import test_portfolio
from india_screener import portfolio as pf, db as dbm, storage
from pathlib import Path


class Integrity(unittest.TestCase):
    setUp = test_portfolio.Ledger.setUp
    tearDown = test_portfolio.Ledger.tearDown
    add = test_portfolio.Ledger.add

    def test_delete_and_correct_roll_back_later_oversell(self):
        buy = self.add('BUY', 'INFY', '2025-01-01', quantity=10, price=100)
        self.add('SELL', 'INFY', '2025-01-02', quantity=8, price=120)
        before = pf.transactions(self.db)
        with self.assertRaises(pf.LedgerError):
            pf.delete(buy, self.db)
        with self.assertRaises(pf.LedgerError):
            pf.correct(buy, self.db, quantity=5)
        self.assertEqual(pf.transactions(self.db), before)
        pf.correct(buy, self.db, price=90)
        self.assertEqual(pf.positions(self.db)['INFY']['realised'], Decimal(240))
        with dbm.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ledger_audit').fetchone()[0], 3)

    def test_interrupt_during_replay_does_not_publish_insert(self):
        with mock.patch.object(pf, '_replay', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.add('BUY', 'INFY', '2025-01-01', quantity=10, price=100)
        self.assertEqual(pf.transactions(self.db), [])

    def test_fractional_entitlement_settlement_and_basis(self):
        self.add('BUY', 'ITC', '2025-01-01', quantity=3, price=400)
        self.add('BONUS', 'ITC', '2025-02-01', ratio='1:2', amount=150)
        p = pf.positions(self.db)['ITC']
        self.assertEqual(p['quantity'], 4)
        # Original 1200 basis: 0.5 of 4.5 shares disposed for 150.
        self.assertAlmostEqual(float(p['invested']), 1066.6666666667)
        self.assertAlmostEqual(float(p['realised']), 16.6666666667)
        self.assertEqual(p['invested'] + Decimal(150) - p['realised'], Decimal(1200))

    def test_fractional_lots_with_integral_total_need_no_cash(self):
        self.add('BUY', 'ITC', '2025-01-01', quantity=1, price=100)
        self.add('BUY', 'ITC', '2025-01-02', quantity=1, price=200)
        self.add('BONUS', 'ITC', '2025-02-01', ratio='1:2')
        self.add('SELL', 'ITC', '2025-03-01', quantity=2, price=120)
        p = pf.positions(self.db)['ITC']
        self.assertEqual(p['quantity'], 1)
        self.assertAlmostEqual(float(p['realised']), 73.3333333333)

    def test_import_all_or_nothing(self):
        text = ('symbol,trade_date,trade_type,quantity,price,trade_id\n'
                'INFY,2025-01-01,buy,10,100,1\nINFY,2025-01-02,sell,11,100,2\n')
        with self.assertRaises(pf.LedgerError):
            pf.import_zerodha_tradebook(text, self.db)
        self.assertEqual(pf.transactions(self.db), [])

    def test_repeatable_migration_keeps_original_records(self):
        raw = sqlite3.connect(self.db)
        raw.executescript(dbm.SCHEMA)
        raw.execute("INSERT INTO transactions(trade_date,symbol,kind,quantity,price) VALUES('2025-01-01','INFY','BUY','2','10')")
        raw.commit(); raw.close()
        for _ in range(2):
            with dbm.connect(self.db) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], dbm.SCHEMA_VERSION)
                self.assertEqual(db.execute('SELECT count(*) FROM transactions').fetchone()[0], 1)
        self.assertEqual(pf.positions(self.db)['INFY']['invested'], 20)

    def test_atomic_json_retry_and_failure_keep_previous(self):
        path = Path(self.tmp.name)/'record.json'
        storage.write_json(path, {'value':1})
        replace = storage.os.replace
        attempts = []
        def retry_once(source, target):
            attempts.append(1)
            if len(attempts) == 1: raise PermissionError()
            return replace(source, target)
        with mock.patch.object(storage.os, 'replace', side_effect=retry_once) as move, mock.patch.object(storage.time, 'sleep'):
            storage.write_json(path, {'value':2})
            self.assertEqual(move.call_count, 2)
        self.assertEqual(storage.read_json(path), {'value':2})
        with mock.patch.object(storage.os, 'replace', side_effect=PermissionError()), mock.patch.object(storage.time, 'sleep'):
            with self.assertRaises(PermissionError):
                storage.write_json(path, {'value':3})
        self.assertEqual(storage.read_json(path), {'value':2})
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_concurrent_sells_cannot_both_spend_the_same_shares(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        self.add('BUY','TEST','2025-01-01',quantity=10,price=100)
        ready=threading.Barrier(2)
        def sell():
            ready.wait()
            try:return self.add('SELL','TEST','2025-02-01',quantity=8,price=100)
            except pf.LedgerError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:sell(), range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(pf.positions(self.db)['TEST']['quantity'],2)

    def test_v2_migration_preserves_all_existing_runtime_tables(self):
        from india_screener import config, outbox
        cfg=config.load_config();cfg['notify']={'windows_toast':False}
        with dbm.connect(self.db) as db:
            db.execute("INSERT INTO news(symbol,title,title_key,url,published_utc) VALUES('TEST','title','key','https://example.test','2026-01-01T00:00:00+00:00')")
            db.execute("INSERT INTO events(symbol,event_session,status) VALUES('TEST','2026-01-01','dip_confirmed')")
            db.execute("INSERT INTO recommendation_log(logged_at,symbol,reason,held,shortlisted) VALUES('2026-01-01','TEST','shortlisted',0,1)")
            aid=outbox.enqueue('title','body','key',cfg,db)
            db.execute("INSERT INTO app_notifications(created_at,kind,title) VALUES('2026-01-01','program','test')")
            db.execute("INSERT INTO headline_audit VALUES(1,?,1,1,'queued','review','reason')",(aid,))
            db.execute("INSERT INTO alert_feedback VALUES(?,'useful','','2026-01-01')",(aid,))
            db.execute("INSERT INTO signal_state VALUES('TEST','watch','2026-01-01')")
            db.execute("INSERT INTO signal_evidence(symbol) VALUES('TEST')")
            db.execute("INSERT INTO signal_news VALUES(1,1)")
            db.execute("INSERT INTO research_observation(observed_at,day,symbol,input_hash,analysis,financials,settings) VALUES('2026-01-01','2026-01-01','TEST','hash','{}','{}','{}')")
            db.execute('DROP TABLE research_context')
            db.execute('PRAGMA user_version=1')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before={name:[tuple(r) for r in db.execute('SELECT * FROM '+name)] for name in tables}
        for _ in range(2):
            with dbm.connect(self.db) as db:
                for name,rows in before.items():
                    self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+name)],rows,name)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM research_context').fetchone()[0],0)
