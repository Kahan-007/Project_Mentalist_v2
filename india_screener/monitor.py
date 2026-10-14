"""Independent continuous news, Telegram and delivery workers, with one owner per data directory."""
import datetime as dt
import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import appnotify, config, daily, db as dbm, news, outbox, storage, telegram, tracker, net, recommendations, news_alerts

STOP = threading.Event()
_threads = []
_lockfile = None
_guard = threading.Lock()
_state_lock = threading.Lock()
_state = {}


def _health(key, value):
    with _state_lock:
        _state[key] = value
        _state['heartbeat'] = config.now_ist().isoformat()
        storage.write_json(config.DATA / 'monitor_status.json', _state)


def status():
    s = storage.read_json(config.DATA / 'monitor_status.json') or {}
    try:
        age = (config.now_ist()-dt.datetime.fromisoformat(s['heartbeat'])).total_seconds()
    except (ValueError, KeyError):
        age = 999999
    from .quality import source_health
    return {**s, 'running': 0 <= age < 90, 'sources':source_health(s), 'telegram': telegram.status(), 'delivery': outbox.status()}


def materiality(head):
    from .materiality import assess
    result = assess(head)
    return result['label'], result['reason']


def ingest(item, heads, cfg):
    from . import fundamentals
    from .materiality import assess
    now, count = config.now_ist(), 0
    record = fundamentals.load(item['symbol'])
    with dbm.connect() as db:
        for h in heads:
            key = tracker.title_key(h['title'])
            if h.get('source') == 'NSE corporate announcement':
                key = 'nse:' + hashlib.sha256(h['url'].encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO news(symbol,title,title_key,url,source,published_utc,categories) VALUES(?,?,?,?,?,?,?)',
                       (item['symbol'], h['title'], key, h['url'], h.get('source',''), h['published_utc'], json.dumps(h.get('categories', []))))
            nid = db.execute('SELECT id FROM news WHERE symbol=? AND title_key=?', (item['symbol'],key)).fetchone()[0]
            audited = db.execute('SELECT disposition FROM headline_audit WHERE news_id=?', (nid,)).fetchone()
            if audited:
                if audited['disposition'] != 'future':
                    continue
                db.execute('DELETE FROM headline_audit WHERE news_id=?', (nid,))
                db.execute('UPDATE news SET published_utc=? WHERE id=?', (h['published_utc'], nid))
            try:
                published = dt.datetime.fromisoformat(h['published_utc'])
                if published.tzinfo is None:
                    raise ValueError('timezone required')
                age, epoch = (now-published).total_seconds(), published.timestamp()
                disposition = 'future' if age < 0 else 'stale' if age > cfg['monitor']['headline_max_age_minutes']*60 else 'queued'
            except (ValueError, TypeError):
                epoch, disposition = None, 'invalid timestamp'
            event_key = 'headline:'+item['symbol']+':'+hashlib.sha256(key.encode()).hexdigest()
            existing = db.execute('SELECT id FROM alert_outbox WHERE event_key=?', (event_key,)).fetchone()
            triage = assess(h, record)
            push, filter_reason = news_alerts.headline_push(item, triage, cfg)
            aid = existing['id'] if existing else None
            if existing:
                disposition = 'previously queued'
            elif disposition == 'queued':
                if not push:
                    disposition = 'filtered'
                    db.execute('INSERT INTO headline_audit VALUES(?,?,?,?,?,?,?)',
                               (nid,None,now.timestamp(),epoch,disposition,triage['label'],filter_reason))
                    continue
                title = f"{item['symbol']}: {triage['label']}"
                body = (f"{h['title']}\nSource: {h.get('source') or 'unknown'} ({triage['source_type']})\n"
                        f"Published: {h['published_utc']}\nDetected: {now.isoformat()}\n"
                        f"{'You hold this stock.' if item['held'] else 'On your shortlist.'}\n"
                        f"{triage['reason']}\n{filter_reason}\n{triage.get('context') or ''}\n{h['url']}\n"
                        'Risk review only; this headline does not create a Buy/Sell recommendation. No trade executed.')
                aid = outbox.enqueue(title, body, event_key, cfg, db)
                body += f'\nAlert #{aid}. Feedback: /feedback {aid} useful (or noise, incorrect, duplicate)'
                db.execute('UPDATE alert_outbox SET body=? WHERE id=?', (body,aid))
                db.execute('INSERT INTO app_notifications(created_at,kind,title,body,symbol) VALUES(?,?,?,?,?)',
                           (now.isoformat(), 'stock', title, body, item['symbol']))
                count += 1
            db.execute('INSERT INTO headline_audit VALUES(?,?,?,?,?,?,?)',
                       (nid,aid,now.timestamp(),epoch,disposition,triage['label'],triage['reason']))
    return count


def scan_once():
    cfg = config.load_config()
    items = daily.watchlist()
    total, errors = 0, {}
    # Bound both concurrency and request volume. Large watchlists are spread across rounds.
    bucket = int(time.time()//max(60, cfg['monitor']['poll_seconds']))
    chunks = max(1, (len(items)+19)//20)
    items = [it for i,it in enumerate(items) if i % chunks == bucket % chunks]
    with ThreadPoolExecutor(max_workers=min(4, max(1, cfg['workers']))) as pool:
        tasks = {pool.submit(news.fetch_news, it['symbol'], it['name'], it['aliases'], days=1): it for it in items}
        for future in as_completed(tasks):
            it = tasks[future]
            try:
                total += ingest(it, future.result(), cfg)
            except Exception as exc:
                errors[it['symbol']] = type(exc).__name__
    _health('news', {'checked_at': config.now_ist().isoformat(), 'tracked_this_round': len(items), 'new_alerts': total, 'errors': errors})
    return total


def scan_nse_once():
    cfg, items = config.load_config(), daily.watchlist()
    heads = news.parse_nse(net.fetch(news.NSE_RSS), items) if items else {}
    count = sum(ingest(item, heads.get(item['symbol'], []), cfg) for item in items)
    _health('nse', {'checked_at': config.now_ist().isoformat(), 'tracked': len(items), 'new_alerts': count})
    return count


def scan_signals_once():
    from . import fundamentals, scoring, research, server, prices
    items = daily.watchlist()
    cfg = config.load_config()
    st = {**scoring.DEFAULT_SETTINGS, **cfg.get('scoring', {})}
    ranks, analyses, records, errors = server.size_ranks(), {}, {}, {}
    # Bound requests just like the headline worker. Larger lists rotate, never hundreds of quotes/minute.
    bucket = int(time.time()//60)
    chunks = max(1, (len(items)+19)//20)
    items = [it for i,it in enumerate(items) if i % chunks == bucket % chunks]

    def observe(it):
        rec = fundamentals.load(it['symbol'])
        if rec:
            rec = news_alerts.quote_record(rec, prices.quote_now(it['symbol'], it.get('exchange', 'NSE')), cfg)
        analysis = scoring.score_company(rec, st, ranks.get(it['symbol'])) if rec else None
        return rec, analysis

    with ThreadPoolExecutor(max_workers=min(4, max(1, cfg['workers']))) as pool:
        tasks = {pool.submit(observe, it):it for it in items}
        for future in as_completed(tasks):
            it = tasks[future]
            try:
                rec, analysis = future.result()
                records[it['symbol']] = rec
                analyses[it['symbol']] = (analysis, None if rec else 'financials unavailable')
            except Exception as exc:
                # A failed/stale quote cannot manufacture a financial signal or overwrite its baseline.
                errors[it['symbol']] = type(exc).__name__
    observed = [it for it in items if it['symbol'] in analyses]
    for it in observed:
        try:
            # Keep the daily research archive tied to saved fundamentals, rather than duplicating
            # full statements for every changing intraday tick. Live decisions have their own ledger.
            saved = fundamentals.load(it['symbol'])
            saved_analysis = scoring.score_company(saved, st, ranks.get(it['symbol'])) if saved else None
            research.capture(it, saved, saved_analysis, st)
        except Exception as exc:
            errors['archive:'+it['symbol']] = type(exc).__name__
    pushed = recommendations.log_signal_changes(observed, analyses, cfg=cfg, records=records, st=st,
                                                size_ranks=ranks, indicative=True)
    _health('signals', {'checked_at': config.now_ist().isoformat(), 'tracked': len(items),
                        'observed':len(observed), 'new_alerts':pushed, 'errors':errors,
                        'price_basis':'indicative quotes; daily event studies still use completed sessions'})
    return pushed


def _loop(name, fn, interval):
    failures = 0
    while not STOP.is_set():
        try:
            fn()
            failures = 0
            _health(name+'_error', None)
        except Exception as exc:
            failures += 1
            _health(name+'_error', type(exc).__name__)
        STOP.wait(min(300, interval * 2**min(failures, 5)) + random.random())


def start():
    global _lockfile, _threads
    with _guard:
        if any(t.is_alive() for t in _threads):
            return True
        config.ensure_dirs()
        f = open(config.DATA / 'monitor.lock', 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        _lockfile = f
        STOP.clear()
        cfg = config.load_config()
        news_alerts.apply_pending_filter(cfg)
        poll = max(60, float(cfg['monitor']['poll_seconds']))
        # The official RSS declares ttl=5 minutes. Google latency is controlled by its publisher/indexer.
        jobs = [('news', scan_once, poll), ('nse', scan_nse_once, 300),
                ('telegram', telegram.poll_once, 1), ('delivery', outbox.flush, 2), ('signals', scan_signals_once, 60)]
        _threads = [threading.Thread(target=_loop, args=j, daemon=True, name='mentalist-'+j[0]) for j in jobs]
        for t in _threads:
            t.start()
        return True


def run():
    if not start():
        raise SystemExit('A monitor already owns this data directory.')
    print('Mentalist monitor running. Ctrl+C to stop.', flush=True)
    try:
        while not STOP.wait(1):
            pass
    except KeyboardInterrupt:
        STOP.set()
