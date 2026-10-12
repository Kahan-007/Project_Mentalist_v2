"""Durable per-channel delivery. Retries survive restarts; network timeouts can duplicate a send."""
import json
import time
import uuid
import hashlib

from . import config, db as dbm

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_outbox(
 id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_delivery(
 id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL REFERENCES alert_outbox(id),
 channel TEXT NOT NULL, destination TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, claim TEXT, last_error TEXT, sent_at REAL,
 UNIQUE(alert_id, channel, destination)
);
CREATE TABLE IF NOT EXISTS signal_state(symbol TEXT PRIMARY KEY, signal TEXT, updated_at TEXT);
"""


def enqueue(title, body, key=None, cfg=None, db=None):
    if db is None:
        with dbm.connect() as conn:
            return enqueue(title, body, key, cfg, conn)
    cfg = cfg or config.load_config()
    key = key or str(uuid.uuid4())
    db.execute("INSERT OR IGNORE INTO alert_outbox(event_key,title,body,created_at) VALUES(?,?,?,?)",
               (key, title, body, time.time()))
    aid = db.execute("SELECT id FROM alert_outbox WHERE event_key=?", (key,)).fetchone()[0]
    n = cfg['notify']
    channels = {'log': 'local'}
    if n.get('telegram_bot_token') and n.get('telegram_chat_id'):
        channels['telegram'] = str(n['telegram_chat_id'])
    if n.get('ntfy_topic'):
        channels['ntfy'] = json.dumps([n.get('ntfy_server', 'https://ntfy.sh'), n['ntfy_topic']])
    if n.get('discord_webhook'):
        channels['discord'] = hashlib.sha256(n['discord_webhook'].encode()).hexdigest()
    import sys
    if n.get('windows_toast') and sys.platform == 'win32':
        channels['toast'] = 'local'
    for channel, dest in channels.items():
        db.execute("INSERT OR IGNORE INTO alert_delivery(alert_id,channel,destination) VALUES(?,?,?)", (aid, channel, dest))
    return aid


def flush(cfg=None, limit=30):
    from . import notify
    cfg = cfg or config.load_config()
    problems = []
    for _ in range(limit):
        now, claim = time.time(), str(uuid.uuid4())
        with dbm.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT d.*,a.title,a.body FROM alert_delivery d JOIN alert_outbox a ON a.id=d.alert_id "
                             "WHERE d.status='pending' AND d.next_attempt<=? AND d.lease_until<=? ORDER BY d.id LIMIT 1", (now, now)).fetchone()
            if not row:
                break
            db.execute("UPDATE alert_delivery SET lease_until=?,claim=?,attempts=attempts+1 WHERE id=?", (now+120, claim, row['id']))
        try:
            notify.deliver(row['channel'], row['destination'], row['title'], row['body'], cfg)
        except Exception as exc:
            # Channel code guarantees no credentials in exceptions.
            err = f"{row['channel']}: {type(exc).__name__}"
            problems.append(err)
            delay = min(3600, 5 * 2 ** min(row['attempts'], 10))
            delay = max(delay, getattr(exc, 'retry_after', 0))
            with dbm.connect() as db:
                db.execute("UPDATE alert_delivery SET status='pending',next_attempt=?,lease_until=0,last_error=? WHERE id=? AND claim=?",
                           (now+delay, err, row['id'], claim))
        else:
            with dbm.connect() as db:
                db.execute("UPDATE alert_delivery SET status='sent',sent_at=?,lease_until=0,last_error=NULL WHERE id=? AND claim=?",
                           (time.time(), row['id'], claim))
    return problems


def status():
    with dbm.connect() as db:
        return {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM alert_delivery GROUP BY status')}
