"""SQLite schema for the ledger, news and event tracker."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions(
    id INTEGER PRIMARY KEY,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'NSE',
    kind TEXT NOT NULL CHECK(kind IN ('BUY','SELL','DIVIDEND','SPLIT','BONUS')),
    quantity TEXT NOT NULL DEFAULT '0',     -- Decimal as text: exact
    price TEXT NOT NULL DEFAULT '0',
    fees TEXT NOT NULL DEFAULT '0',
    amount TEXT NOT NULL DEFAULT '0',       -- DIVIDEND total cash received
    ratio_num INTEGER, ratio_den INTEGER,   -- SPLIT new:old, BONUS bonus:held
    external_id TEXT UNIQUE,                -- broker trade id, for de-duplicated imports
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS news(
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    title_key TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    published_utc TEXT NOT NULL,
    categories TEXT NOT NULL DEFAULT '[]',
    event_session TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, title_key)
);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'NSE',
    event_session TEXT NOT NULL,
    status TEXT NOT NULL,          -- provisional | no_dip | dip_confirmed | levelled | expired | insufficient_data
    detail TEXT NOT NULL DEFAULT '{}',
    notified_dip INTEGER NOT NULL DEFAULT 0,
    notified_level INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, event_session)
);
CREATE TABLE IF NOT EXISTS recommendation_log(   -- append-only: rows are never edited or deleted
    id INTEGER PRIMARY KEY,
    logged_at TEXT NOT NULL,       -- IST timestamp
    symbol TEXT NOT NULL,
    reason TEXT NOT NULL,          -- shortlisted | removed_from_shortlist | alert_dip | alert_levelled | bought | sold | trades_imported
    held INTEGER NOT NULL,
    shortlisted INTEGER NOT NULL,
    price REAL, price_date TEXT, price_source TEXT,
    buffett REAL, munger REAL, lynch REAL,
    matches TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',  -- the alert text, or trade details
    headlines TEXT NOT NULL DEFAULT '[]',
    analysis TEXT NOT NULL DEFAULT '{}'   -- the full scoring output at that moment (every criterion + value)
);
CREATE INDEX IF NOT EXISTS reclog_symbol ON recommendation_log(symbol, logged_at);
CREATE INDEX IF NOT EXISTS news_unresolved ON news(symbol, event_session);
CREATE INDEX IF NOT EXISTS events_open ON events(status);
"""


@contextmanager
def connect(path=None):
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    from .outbox import SCHEMA as OUTBOX_SCHEMA
    db.executescript(OUTBOX_SCHEMA)
    from .appnotify import SCHEMA as NOTIFICATION_SCHEMA
    db.executescript(NOTIFICATION_SCHEMA)
    from .quality import SCHEMA as QUALITY_SCHEMA
    db.executescript(QUALITY_SCHEMA)
    from .research import SCHEMA as RESEARCH_SCHEMA
    db.executescript(RESEARCH_SCHEMA)
    from .news_alerts import SCHEMA as NEWS_ALERT_SCHEMA
    db.executescript(NEWS_ALERT_SCHEMA)
    try:
        with db:
            yield db
    finally:
        db.close()
