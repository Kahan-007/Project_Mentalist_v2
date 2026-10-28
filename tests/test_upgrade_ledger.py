"""Ledger failures leave no committed transactions or audit entries."""
import sqlite3
from decimal import Decimal
from unittest import mock
from tests.test_portfolio import Ledger
from india_screener import portfolio as pf, db as dbm, storage
from pathlib import Path


class Integrity(Ledger):
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
        with mock.patch.object(storage.os, 'replace', side_effect=[PermissionError(), None]) as move, mock.patch.object(storage.time, 'sleep'):
            storage.write_json(path, {'value':2})
            self.assertEqual(move.call_count, 2)
        with mock.patch.object(storage.os, 'replace', side_effect=PermissionError()), mock.patch.object(storage.time, 'sleep'):
            with self.assertRaises(PermissionError):
                storage.write_json(path, {'value':3})
        self.assertEqual(storage.read_json(path), {'value':1})
        self.assertEqual(list(path.parent.glob('*.tmp')), [])
