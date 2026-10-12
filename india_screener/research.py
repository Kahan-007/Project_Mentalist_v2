"""Immutable forward observations and descriptive outcomes from cached adjusted daily prices."""
import datetime as dt
import hashlib
import json
import math

from . import config, db as dbm, recommendations, storage, prices

SCHEMA = '''
CREATE TABLE IF NOT EXISTS research_observation(
 id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, day TEXT NOT NULL, symbol TEXT NOT NULL,
 input_hash TEXT NOT NULL, signal TEXT, held INTEGER, shortlisted INTEGER,
 analysis TEXT NOT NULL, financials TEXT NOT NULL, settings TEXT NOT NULL,
 UNIQUE(day,symbol,input_hash)
);
'''


def capture(item, record, analysis, settings):
    clean = recommendations._clean
    if record:
        record = {**record,'exchange':item.get('exchange','NSE')}
    inputs = json.dumps(clean({'record':record,'settings':settings,'held':item['held'],'shortlisted':item['shortlisted'],
                              'algorithm':(analysis or {}).get('algorithm_version')}),sort_keys=True)
    stamp = config.now_ist().isoformat()
    signal, _ = recommendations.assess(item['held'], item['shortlisted'], analysis, settings)
    with dbm.connect() as db:
        db.execute('INSERT OR IGNORE INTO research_observation(observed_at,day,symbol,input_hash,signal,held,shortlisted,analysis,financials,settings) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (stamp,stamp[:10],item['symbol'],hashlib.sha256(inputs.encode()).hexdigest(),signal,
                    int(item['held']),int(item['shortlisted']),json.dumps(clean(analysis)),json.dumps(clean(record)),json.dumps(settings)))


def outcome(observed_at, stock, benchmark, horizon):
    """Start at first common completed close strictly after observation day; no same-day look-ahead.
    Both endpoints use the same fetched series so corporate-action adjustment scales agree.
    Missing stock dates within the benchmark window reject the result instead of shortening the horizon.
    """
    day = dt.datetime.fromisoformat(observed_at).astimezone(config.IST).date().isoformat()
    smap = {r['date']:r['adjclose'] for r in stock}
    bmap = {r['date']:r['adjclose'] for r in benchmark}
    if not bmap or not any(d <= day for d in bmap):
        return {'status':'baseline history unavailable'}
    now = config.now_ist()
    last_completed = now.date() if now.hour >= config.EOD_READY_HOUR else now.date()-dt.timedelta(days=1)
    dates = sorted(d for d in bmap if day < d <= last_completed.isoformat())
    if not dates or dates[0] not in smap:
        return {'status':'awaiting entry data'}
    if len(dates) <= horizon:
        return {'status':'awaiting horizon'}
    window = dates[:horizon+1]
    if any(d not in smap for d in window):
        return {'status':'incomplete stock history'}
    if any(not math.isfinite(v) or v <= 0 for d in window for v in (smap[d],bmap[d])):
        return {'status':'invalid prices'}
    sr, br = smap[window[-1]]/smap[window[0]]-1, bmap[window[-1]]/bmap[window[0]]-1
    peak, drawdown = smap[window[0]], 0
    for d in window:
        peak = max(peak,smap[d]); drawdown = min(drawdown,smap[d]/peak-1)
    return {'status':'observed','entry_date':window[0],'end_date':window[-1], 'stock_return':sr,
            'benchmark_return':br,'excess_return':sr-br,'max_drawdown':drawdown}


def cached(symbol, exchange, cfg):
    ysym = prices.yahoo_symbol(symbol,exchange)
    path = config.CACHE/'prices'/(ysym.replace('^','IDX_')+f"_{cfg['history_range']}.json.gz")
    data = storage.read_json(path) or {}
    return data.get('bars',[]),data.get('fetched_at')


def report():
    cfg = config.load_config()
    benchmark, fetched = cached(cfg['benchmark'],'NSE',cfg)
    # Require the named benchmark; do not silently substitute an ETF with different distributions.
    with dbm.connect() as db:
        total = db.execute('SELECT count(*) FROM research_observation').fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT id,observed_at,symbol,signal,financials FROM research_observation ORDER BY id DESC LIMIT 100')]
    for row in rows:
        rec = json.loads(row.pop('financials')) or {}
        stock, stamp = cached(row['symbol'],rec.get('exchange','NSE'),cfg)
        row['price_data_fetched_at'] = stamp
        row['outcomes'] = {str(h):outcome(row['observed_at'],stock,benchmark,h) for h in (20,60,120,252)}
    return {'total':total,'recent':rows,'benchmark':cfg['benchmark'],'benchmark_fetched_at':fetched,
            'note':'Forward observations, not a strategy backtest. Outcomes use the next session close and cached adjusted closes; they exclude trading costs, fills, taxes and portfolio construction. Repeated daily observations overlap and are not independent trials. No historical signals are invented.'}
