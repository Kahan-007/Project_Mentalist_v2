"""Stores headlines, groups them into per-session events, and moves events through their lifecycle:

  provisional --(abnormal drop confirmed)--> dip_confirmed --(levelled checks pass)--> levelled
       |                                          |--(newer confirmed dip)-------> superseded
       '--(full window, no abnormal drop)-> no_dip '--(too long, never levelled)--> expired
"""
import json
import re

from . import db as dbm
from . import events as ev


def title_key(title):
    return re.sub(r"\W+", " ", title.lower()).strip()[:300]


def store_news(symbol, items, path=None):
    added = 0
    with dbm.connect(path) as db:
        for it in items:
            cur = db.execute(
                "INSERT OR IGNORE INTO news(symbol,title,title_key,url,source,published_utc,categories) VALUES(?,?,?,?,?,?,?)",
                (symbol, it["title"], title_key(it["title"]), it["url"], it.get("source", ""),
                 it["published_utc"], json.dumps(it.get("categories", []))))
            added += cur.rowcount
    return added


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
    """Advance all open events for one symbol. Returns alert dicts for newly confirmed dips / levellings."""
    alerts = []
    e_cfg, l_cfg = cfg["event"], cfg["levelling"]
    dates = [b["date"] for b in stock_bars]
    with dbm.connect(path) as db:
        open_events = db.execute(
            "SELECT * FROM events WHERE symbol=? AND (status IN ('provisional','dip_confirmed') OR "
            "(status='levelled' AND (notified_dip=0 OR notified_level=0))) ORDER BY event_session",
            (symbol,)).fetchall()
    for row in open_events:
        session, status = row["event_session"], row["status"]
        new_status, detail = status, json.loads(row["detail"])
        if status == "provisional":
            res = ev.assess_dip(stock_bars, bench_bars, session, e_cfg)
            if res["status"] in ("dip_confirmed", "no_dip", "insufficient_data"):
                new_status = res["status"]
            detail = {"dip": res}
        if new_status == "dip_confirmed":
            lev = ev.assess_levelling(stock_bars, bench_bars, session, e_cfg, l_cfg)
            detail["levelling"] = lev
            since = len([d for d in dates if d >= session])
            if lev.get("levelled"):
                new_status = "levelled"
            elif since > e_cfg["expire_after_sessions"]:
                new_status = "expired"
        with dbm.connect(path) as db:
            db.execute("UPDATE events SET status=?, detail=?, updated_at=datetime('now') WHERE id=?",
                       (new_status, json.dumps(detail, default=str), row["id"]))
            if new_status == "dip_confirmed" and status == "provisional":
                # A fresh confirmed dip makes older 'waiting to level' events ambiguous; retire them.
                db.execute("UPDATE events SET status='superseded', updated_at=datetime('now') "
                           "WHERE symbol=? AND status='dip_confirmed' AND event_session<?", (symbol, session))
            if new_status in ("dip_confirmed", "levelled") and not row["notified_dip"]:
                alerts.append({"type": "dip", "symbol": symbol, "session": session, "detail": detail})
                if publish:
                    queue_event(alerts[-1], cfg, db)
                    db.execute("UPDATE events SET notified_dip=1 WHERE id=?", (row["id"],))
            if new_status == "levelled" and not row["notified_level"]:
                alerts.append({"type": "levelled", "symbol": symbol, "session": session, "detail": detail})
                if publish:
                    queue_event(alerts[-1], cfg, db)
                    db.execute("UPDATE events SET notified_level=1 WHERE id=?", (row["id"],))
    return alerts


def queue_event(alert, cfg, db):
    from . import outbox
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
    outbox.enqueue(title, body, f"event:{alert['symbol']}:{alert['session']}:{alert['type']}", cfg, db)


def list_events(statuses=None, path=None):
    q, args = "SELECT * FROM events", ()
    if statuses:
        q += " WHERE status IN (%s)" % ",".join("?" * len(statuses))
        args = tuple(statuses)
    with dbm.connect(path) as db:
        return [dict(r) for r in db.execute(q + " ORDER BY event_session DESC, symbol", args)]
