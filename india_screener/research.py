"""Immutable forward observations and descriptive outcomes from cached adjusted daily prices."""
import datetime as dt
import hashlib
import json
import math
import statistics

from . import config, db as dbm, recommendations, storage, prices

SCHEMA = '''
CREATE TABLE IF NOT EXISTS research_observation(
 id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, day TEXT NOT NULL, symbol TEXT NOT NULL,
 input_hash TEXT NOT NULL, signal TEXT, held INTEGER, shortlisted INTEGER,
 analysis TEXT NOT NULL, financials TEXT NOT NULL, settings TEXT NOT NULL,
 UNIQUE(day,symbol,input_hash)
);
CREATE TABLE IF NOT EXISTS research_context(
 observation_id INTEGER PRIMARY KEY REFERENCES research_observation(id),
 benchmark TEXT NOT NULL, origin TEXT NOT NULL, model_version TEXT NOT NULL,
 price_evidence TEXT NOT NULL, data_vintage TEXT NOT NULL, quality TEXT NOT NULL
);
'''


def capture(item, record, analysis, settings, *, db=None, signal=None, origin='saved model observation', benchmark=None):
    """Archive actual available inputs. A supplied connection joins the signal transaction."""
    benchmark = benchmark or config.load_config()['benchmark']
    clean = recommendations._clean
    if record:
        record = {**record,'exchange':item.get('exchange','NSE')}
    signal = signal if signal is not None else recommendations.assess(item['held'], item['shortlisted'], analysis, settings)[0]
    inputs = json.dumps(clean({'record':record,'settings':settings,'held':item['held'],'shortlisted':item['shortlisted'],
                              'algorithm':(analysis or {}).get('algorithm_version'), 'benchmark':benchmark,
                              'origin':origin, 'signal':signal}),sort_keys=True)
    stamp = config.now_ist().isoformat()
    def insert(connection):
        digest = hashlib.sha256(inputs.encode()).hexdigest()
        cur = connection.execute('INSERT OR IGNORE INTO research_observation(observed_at,day,symbol,input_hash,signal,held,shortlisted,analysis,financials,settings) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (stamp,stamp[:10],item['symbol'],digest,signal,
                    int(item['held']),int(item['shortlisted']),json.dumps(clean(analysis)),json.dumps(clean(record)),json.dumps(clean(settings))))
        if cur.rowcount:
            rec = record or {}
            connection.execute('INSERT INTO research_context VALUES(?,?,?,?,?,?,?)',
                (cur.lastrowid,benchmark,origin,(analysis or {}).get('algorithm_version','unknown'),
                 json.dumps(clean(rec.get('meta') or {})),
                 json.dumps(clean({k:rec.get(k) for k in ('imported_at','source','years','quarter_source','quarter_fetched_at','refresh_provenance')})),
                 json.dumps(clean((analysis or {}).get('data_quality',{})))))
        return cur.lastrowid if cur.rowcount else None
    if db is not None:
        return insert(db)
    with dbm.connect() as connection:
        return insert(connection)


def outcome(observed_at, stock, benchmark, horizon, cost_bps=10):
    """Start at first common completed close strictly after observation day; no same-day look-ahead.
    Both endpoints use the same fetched series so corporate-action adjustment scales agree.
    Missing stock dates within the benchmark window reject the result instead of shortening the horizon.
    """
    if isinstance(horizon,bool) or not isinstance(horizon,int) or not 1 <= horizon <= 252:
        raise ValueError('Horizon must be 1–252 sessions')
    if isinstance(cost_bps,bool) or not isinstance(cost_bps,(int,float)) or not math.isfinite(cost_bps) or not 0 <= cost_bps <= 1000:
        raise ValueError('One-way costs must be 0–1000 basis points')
    try:
        observed = dt.datetime.fromisoformat(observed_at)
        if observed.tzinfo is None:
            return {'status':'observation timezone unavailable'}
        day = observed.astimezone(config.IST).date().isoformat()
        for bars in (stock,benchmark):
            dates = [dt.date.fromisoformat(r['date']) for r in bars]
            if len(dates) != len(set(dates)):
                return {'status':'duplicate price sessions'}
    except (ValueError, TypeError, KeyError):
        return {'status':'invalid price dates'}
    smap = {r['date']:r.get('adjclose') for r in stock}
    bmap = {r['date']:r.get('adjclose') for r in benchmark}
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
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0 for d in window for v in (smap[d],bmap[d])):
        return {'status':'invalid prices'}
    sr, br = smap[window[-1]]/smap[window[0]]-1, bmap[window[-1]]/bmap[window[0]]-1
    peak, drawdown = smap[window[0]], 0
    for d in window:
        peak = max(peak,smap[d]); drawdown = min(drawdown,smap[d]/peak-1)
    cost = cost_bps/10000
    net_return = (1+sr)*(1-cost)/(1+cost)-1
    return {'one_way_cost_bps':cost_bps, 'net_stock_return':net_return, 'net_excess_return':net_return-br,
            'status':'observed','entry_date':window[0],'end_date':window[-1], 'stock_return':sr,
            'benchmark_return':br,'excess_return':sr-br,'max_drawdown':drawdown}


def cached(symbol, exchange, cfg):
    ysym = prices.yahoo_symbol(symbol,exchange)
    path = config.CACHE/'prices'/(ysym.replace('^','IDX_')+f"_{cfg['history_range']}.json.gz")
    data = storage.read_json(path) or {}
    return data.get('bars',[]),data.get('fetched_at')


def summarize(rows, horizon):
    """Descriptive outcomes and a nonoverlapping subset, never a trade win rate."""
    observed = [r for r in rows if r['outcomes'][str(horizon)]['status']=='observed']
    independent, last_end = [], {}
    for row in sorted(observed,key=lambda r:(r['outcomes'][str(horizon)]['entry_date'],r['id'])):
        value = row['outcomes'][str(horizon)]
        key = (row['symbol'],row.get('model_version'),row.get('signal'),row.get('origin'))
        if value['entry_date'] > last_end.get(key,''):
            independent.append(row)
            last_end[key] = value['end_date']
    def metrics(sample):
        excess = sorted(r['outcomes'][str(horizon)]['net_excess_return'] for r in sample)
        return {'samples':len(sample), 'median_net_excess_return':statistics.median(excess) if excess else None,
                'outperforming_observation_fraction':sum(v>0 for v in excess)/len(excess) if excess else None,
                'distribution':{'min':excess[0], 'p25':excess[int((len(excess)-1)*.25)],
                                'p75':excess[int((len(excess)-1)*.75)],'max':excess[-1]} if excess else None}
    return {'observations':len(rows),'completed':len(observed),'coverage':len(observed)/len(rows) if rows else 0,
            'all_overlapping_observations':metrics(observed), 'nonoverlapping_per_stock':metrics(independent),
            'evidence':'insufficient evidence for a validated investment-performance claim',
            'note':'Overlap is removed within each stock/model/signal/origin; different stocks still share market exposure. These are observations, not independent trades.'}


def report(cost_bps=10):
    cfg = config.load_config()
    with dbm.connect() as db:
        rows = [dict(r) for r in db.execute('SELECT o.*,c.benchmark,c.origin,c.model_version,c.price_evidence,c.data_vintage,c.quality '
                'FROM research_observation o LEFT JOIN research_context c ON c.observation_id=o.id ORDER BY o.id DESC')]
    cache = {}
    def history(symbol,exchange='NSE'):
        key=(symbol,exchange)
        if key not in cache:
            cache[key]=cached(symbol,exchange,cfg)
        return cache[key]
    for row in rows:
        rec = json.loads(row.pop('financials')) or {}
        analysis = json.loads(row.pop('analysis')) or {}
        row['settings_snapshot'] = json.loads(row.pop('settings'))
        row['matches'] = analysis.get('matches',[])
        row['valuation_assumptions'] = analysis.get('valuation',{})
        for key in ('price_evidence','data_vintage','quality'):
            row[key]=json.loads(row[key]) if row[key] else None
        stock, stamp = history(row['symbol'],rec.get('exchange','NSE'))
        benchmark, fetched = history(row['benchmark']) if row['benchmark'] else ([],None)
        row['price_data_fetched_at'],row['benchmark_fetched_at'] = stamp,fetched
        row['outcomes'] = {str(h):outcome(row['observed_at'],stock,benchmark,h,cost_bps) if row['benchmark'] else
                          {'status':'historical benchmark identity unavailable'} for h in (20,60,120,252)}
    summaries = {str(h):summarize(rows,h) for h in (20,60,120,252)}
    groups = {}
    for dimension in ('signal','model_version','origin','philosophy'):
        grouped = {}
        for row in rows:
            values=row['matches'] if dimension=='philosophy' else [row.get(dimension) or 'unknown']
            for value in values:
                grouped.setdefault(value,[]).append(row)
        groups[dimension]={k:{str(h):summarize(v,h) for h in (20,60,120,252)} for k,v in grouped.items()}
    _, fetched=history(cfg['benchmark'])
    return {'total':len(rows),'recent':rows[:100],'benchmark':cfg['benchmark'],'benchmark_fetched_at':fetched,
            'summary':summaries,'groups':groups,'one_way_cost_bps':cost_bps,
            'note':'Prospective observations, not a strategy backtest. Each new observation freezes its benchmark, model, evidence and settings; legacy rows without a benchmark remain unevaluable. '
                   'Entry is the next completed session close, not a claimed fill. Net returns apply the displayed illustrative cost to entry and exit; taxes and market impact are not modeled. '
                   'Repeated observations overlap. Saved-price model checks and confirmed live transitions are separate cohorts. No historical decisions or past index membership are invented. '
                   'Adjusted data can be revised by providers; the index may exclude dividends while stock adjusted closes include them. No profitability claim is established.'}
