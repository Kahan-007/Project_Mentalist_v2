"""Stores headlines, groups them into per-session events, and moves events through their lifecycle:

  provisional --(abnormal drop confirmed)--> dip_confirmed --(levelled checks pass)--> levelled
       |                                          |--(newer confirmed dip)-------> superseded
       '--(full window, no abnormal drop)-> no_dip '--(too long, never levelled)--> expired
"""
import json
import datetime as dt
import hashlib
import re

from . import db as dbm
from . import events as ev


def title_key(title):
    return re.sub(r"\W+", " ", title.lower()).strip()[:300]


def store_headline(db, symbol, item):
    key = ('nse:'+hashlib.sha256(item['url'].encode()).hexdigest() if item.get('source')=='NSE corporate announcement'
           else title_key(item['title']))
    cur = db.execute('INSERT OR IGNORE INTO news(symbol,title,title_key,url,source,published_utc,categories) VALUES(?,?,?,?,?,?,?)',
                     (symbol,item['title'],key,item['url'],item.get('source',''),item['published_utc'],json.dumps(item.get('categories',[]))))
    old = db.execute('SELECT * FROM news WHERE symbol=? AND title_key=?',(symbol,key)).fetchone()
    if not cur.rowcount:
        try:
            new_time, old_time = (dt.datetime.fromisoformat(v) for v in (item['published_utc'],old['published_utc']))
            if new_time.tzinfo and old_time.tzinfo and new_time < old_time and new_time <= dt.datetime.now(dt.timezone.utc):
                db.execute('UPDATE news SET published_utc=? WHERE id=?',(new_time.astimezone(dt.timezone.utc).isoformat(),old['id']))
                # An already published event is historical evidence; never silently rewrite its attribution.
                event = db.execute('SELECT notified_dip,notified_level FROM events WHERE symbol=? AND event_session=?',
                                   (symbol,old['event_session'])).fetchone()
                if event and not (event['notified_dip'] or event['notified_level']):
                    db.execute('UPDATE news SET event_session=NULL WHERE id=?',(old['id'],))
                    db.execute("UPDATE events SET status='superseded' WHERE symbol=? AND event_session=? AND NOT EXISTS "
                               '(SELECT 1 FROM news WHERE symbol=? AND event_session=?)',
                               (symbol,old['event_session'],symbol,old['event_session']))
        except (TypeError,ValueError):
            pass
    return old['id'], cur.rowcount


def store_news(symbol, items, path=None):
    with dbm.connect(path) as db:
        return sum(store_headline(db,symbol,item)[1] for item in items)


def group_into_events(symbol, exchange, trading_dates, path=None):
    """Assign each unresolved headline to the session it could first be traded on; one event per session."""
    created = 0
    with dbm.connect(path) as db:
        rows = db.execute("SELECT id, published_utc FROM news WHERE symbol=? AND event_session IS NULL", (symbol,)).fetchall()
        for r in rows:
            session = ev.event_session(r["published_utc"], trading_dates)
            if session is None:
                continue   # its session hasn't closed yet; retried on the next run
            db.execute("UPDATE news SET event_session=? WHERE id=?", (session, r["id"]))
            cur = db.execute("INSERT OR IGNORE INTO events(symbol,exchange,event_session,status) VALUES(?,?,?,'provisional')",
                             (symbol, exchange, session))
            created += cur.rowcount
    return created


def headlines_for(symbol, session, path=None):
    with dbm.connect(path) as db:
        return [dict(r) for r in db.execute(
            "SELECT title,url,source,published_utc,categories FROM news WHERE symbol=? AND event_session=? ORDER BY published_utc",
            (symbol, session))]


def evaluate(symbol, stock_bars, bench_bars, cfg, path=None, publish=True):
    """Evaluate newer dips before older stability; event state and deliveries commit together."""
    alerts = []
    e_cfg, l_cfg = cfg['event'],cfg['levelling']
    dates = [b['date'] for b in stock_bars]
    with dbm.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM events WHERE symbol=? AND (status IN ('provisional','dip_confirmed') OR "
            "(status='levelled' AND (notified_dip=0 OR notified_level=0))) ORDER BY event_session", (symbol,))]
        latest_new = None
        for row in rows:
            detail = json.loads(row['detail'])
            row['next_status'] = row['status']
            if row['status']=='provisional':
                result = ev.assess_dip(stock_bars,bench_bars,row['event_session'],e_cfg)
                detail['dip'] = result
                if result['status'] in ('dip_confirmed','no_dip','insufficient_data'):
                    row['next_status']=result['status']
                if row['next_status']=='dip_confirmed':
                    latest_new=row['event_session']
            row['parsed_detail']=detail
        for row in rows:
            session, state, detail = row['event_session'],row['next_status'],row['parsed_detail']
            if latest_new and session < latest_new and state in ('dip_confirmed','levelled'):
                state='superseded'
            if state=='dip_confirmed':
                level = ev.assess_levelling(stock_bars,bench_bars,session,e_cfg,l_cfg)
                detail['levelling']=level
                if level.get('levelled'):
                    state='levelled'
                elif sum(d>=session for d in dates)>e_cfg['expire_after_sessions']:
                    state='expired'
            db.execute("UPDATE events SET status=?,detail=?,updated_at=datetime('now') WHERE id=?",
                       (state,json.dumps(detail,default=str),row['id']))
            for kind, flag in (('dip','notified_dip'),('levelled','notified_level')):
                applicable = state in ('dip_confirmed','levelled') if kind=='dip' else state=='levelled'
                if applicable and not row[flag]:
                    alert={'type':kind,'symbol':symbol,'session':session,'detail':detail}
                    alerts.append(alert)
                    if publish:
                        queue_event(alert,cfg,db)
                        db.execute(f'UPDATE events SET {flag}=1 WHERE id=?',(row['id'],))
    return alerts


def queue_event(alert, cfg, db):
    from . import outbox, news_alerts
    title = f"{alert['symbol']}: " + ('news-linked abnormal drop' if alert['type'] == 'dip' else 'price has levelled out')
    rows = db.execute('SELECT title,url FROM news WHERE symbol=? AND event_session=?', (alert['symbol'], alert['session'])).fetchall()
    dip, level = alert['detail'].get('dip', {}), alert['detail'].get('levelling', {})
    percent = lambda x: 'unavailable' if x is None else f'{x*100:+.2f}%'
    if alert['type'] == 'dip':
        detail = (f"Market-adjusted return: {percent(dip.get('abnormal_return'))}. "
                  f"Stock return: {percent(dip.get('raw_return'))}; benchmark: {percent(dip.get('market_return'))}.\n"
                  f"Required abnormal drop: {percent(-(dip.get('threshold') or 0))}.")
    else:
        detail = (f"Return from pre-event close: {percent(level.get('recovery_vs_pre_event'))}.\n"
                  f"Trough: {level.get('trough_date')}; return at trough: {percent(level.get('trough_vs_pre_event'))}.\n"
                  'Daily stability checks passed; this does not predict a rebound.')
    body = (f"Event session: {alert['session']}\n" + detail + '\n' +
            '\n'.join(f"{r['title']}\n{r['url']}" for r in rows[:4]) +
            '\nSame-session association does not prove causation. Review manually; no order placed.')
    outbox.enqueue(title, body, f"event:{alert['symbol']}:{alert['session']}:{alert['type']}", cfg, db,
                   push=news_alerts.settings(cfg)['mode'] == 'all')


def list_events(statuses=None, path=None):
    q, args = "SELECT * FROM events", ()
    if statuses:
        q += " WHERE status IN (%s)" % ",".join("?" * len(statuses))
        args = tuple(statuses)
    with dbm.connect(path) as db:
        return [dict(r) for r in db.execute(q + " ORDER BY event_session DESC, symbol", args)]
