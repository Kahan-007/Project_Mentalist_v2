"""Local alert audit, latency measurements and user feedback; no third-party telemetry."""
import datetime as dt
import math
import time
import statistics

from . import config, db as dbm

SCHEMA = '''
CREATE TABLE IF NOT EXISTS headline_audit(
 news_id INTEGER PRIMARY KEY REFERENCES news(id), alert_id INTEGER REFERENCES alert_outbox(id),
 detected_at REAL NOT NULL, published_at REAL, disposition TEXT NOT NULL,
 priority TEXT NOT NULL, explanation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_feedback(
 alert_id INTEGER PRIMARY KEY REFERENCES alert_outbox(id), rating TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
'''


def feedback(alert_id, rating, note=''):
    if rating not in ('useful', 'noise', 'incorrect', 'duplicate'):
        raise ValueError('Choose useful, noise, incorrect or duplicate')
    if len(note) > 1000:
        raise ValueError('Feedback note must be at most 1000 characters')
    with dbm.connect() as db:
        if not db.execute('SELECT 1 FROM headline_audit WHERE alert_id=? UNION ALL '
                          "SELECT 1 FROM signal_news s JOIN alert_outbox a ON a.event_key='signal:' || s.recommendation_id "
                          'WHERE a.id=?', (int(alert_id),int(alert_id))).fetchone():
            raise ValueError('That headline alert ID was not found')
        db.execute('INSERT INTO alert_feedback VALUES(?,?,?,?) ON CONFLICT(alert_id) DO UPDATE SET '
                   'rating=excluded.rating,note=excluded.note,updated_at=excluded.updated_at',
                   (int(alert_id), rating, note, config.now_ist().isoformat()))
    return {'saved': True}


def distribution(values):
    values = sorted(v for v in values if v is not None and v >= 0)
    return {'samples':len(values), 'median_seconds': statistics.median(values) if values else None,
            'p95_seconds':values[math.ceil(len(values)*.95)-1] if values else None}


def report(days=7):
    cutoff = time.time()-days*86400
    with dbm.connect() as db:
        rows = [dict(r) for r in db.execute('''SELECT h.*,n.symbol,n.title,n.url,n.source,a.created_at,
            (SELECT min(sent_at) FROM alert_delivery d WHERE d.alert_id=h.alert_id AND d.channel='telegram' AND d.status='sent') AS sent_at,
            f.rating FROM headline_audit h JOIN news n ON n.id=h.news_id
            LEFT JOIN alert_outbox a ON a.id=h.alert_id LEFT JOIN alert_feedback f ON f.alert_id=h.alert_id
            WHERE h.detected_at>=? ORDER BY h.detected_at DESC''', (cutoff,))]
        pending = db.execute("SELECT count(*),min(a.created_at) FROM alert_delivery d JOIN alert_outbox a ON a.id=d.alert_id WHERE d.status='pending' AND d.channel='telegram'").fetchone()
        ratings = {r['rating']:r['n'] for r in db.execute('SELECT f.rating,count(*) AS n FROM alert_feedback f '
                   'JOIN alert_outbox a ON a.id=f.alert_id WHERE a.created_at>=? GROUP BY f.rating', (cutoff,))}
    counts, delivered = {}, {}
    for r in rows:
        counts[r['disposition']] = counts.get(r['disposition'], 0)+1
        r['publication_to_detection_seconds'] = r['detected_at']-r['published_at'] if r['published_at'] else None
        r['queue_to_delivery_seconds'] = r['sent_at']-r['created_at'] if r['sent_at'] else None
        if r['alert_id'] is not None:
            delivered[r['alert_id']] = r['queue_to_delivery_seconds']
    return {'days':days, 'counts':counts, 'feedback':ratings,
            'publication_to_detection':distribution([r['publication_to_detection_seconds'] for r in rows]),
            'queue_to_delivery':distribution(delivered.values()),
            'pending_telegram':pending[0], 'oldest_pending_seconds':time.time()-pending[1] if pending[1] else None,
            'recent':rows[:100], 'note':'Latency starts at the source publication timestamp, which may be delayed or revised. Delivery means API acknowledgement, not phone display. Unseen/missed stories cannot be measured without a reference feed.'}


def source_health(state):
    result = []
    for key, label, limit in [('news','Google News',300),('nse','NSE announcements',900),('signals','Model checks',300)]:
        item = state.get(key) or {}
        try:
            age = (config.now_ist()-dt.datetime.fromisoformat(item['checked_at'])).total_seconds()
        except (ValueError, KeyError, TypeError):
            age = None
        failed = bool(state.get(key+'_error') or item.get('errors'))
        status = 'error' if failed else 'not checked' if age is None else 'stale' if age < 0 or age > limit else 'healthy'
        result.append({'source':label,'state':status,'checked_at':item.get('checked_at'),'age_seconds':age})
    return result
