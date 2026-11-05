"""End-of-day relative value for predefined economic peers."""
import math
import statistics as stats
from datetime import date
from . import config, prices, tracker, db, outbox, recommendations, storage

CURATED_PAIRS = [
    ("PFC", "RECLTD", "Power Finance Twins (HoldCo/Sub)"),
    ("BAJAJFINSV", "BAJFINANCE", "Bajaj HoldCo / NBFC OpCo"),
    ("GRASIM", "ULTRACEMCO", "Birla HoldCo / Cement OpCo"),
    ("TCS", "INFY", "Tier-1 IT Services"),
    ("INFY", "HCLTECH", "Tier-1 IT Services"),
    ("WIPRO", "TECHM", "Turnaround Tier-1 IT"),
    ("LTIM", "PERSISTENT", "High-Growth Mid-Cap IT"),
    ("COFORGE", "MPHASIS", "Mid-Cap IT Services"),
    ("HDFCBANK", "ICICIBANK", "Private Megabanks"),
    ("ICICIBANK", "AXISBANK", "Private Corporate/Retail Banks"),
    ("KOTAKBANK", "HDFCBANK", "High-Multiple Private Banks"),
    ("FEDERALBNK", "IDFCFIRSTB", "Mid-Cap Private Banks"),
    ("SBIN", "BANKBARODA", "Large PSU Banks"),
    ("PNB", "CANBK", "PSU Banks"),
    ("MUTHOOTFIN", "MANAPPURAM", "Gold Loan NBFCs"),
    ("CHOLAFIN", "SHRIRAMFIN", "Vehicle & Retail NBFCs"),
    ("BPCL", "HPCL", "Oil Marketing Companies (OMCs)"),
    ("IOC", "BPCL", "Oil Marketing Companies (OMCs)"),
    ("ONGC", "OIL", "Upstream Oil & Gas"),
    ("HEROMOTOCO", "BAJAJ-AUTO", "Two-Wheeler Autos"),
    ("TVSMOTOR", "BAJAJ-AUTO", "Two-Wheeler Autos"),
    ("TATASTEEL", "JSWSTEEL", "Integrated Steel Makers"),
    ("HINDALCO", "VEDL", "Non-Ferrous Metals"),
    ("ULTRACEMCO", "AMBUJACEM", "Pan-India Cement"),
    ("SHREECEM", "DALBHARAT", "Cement Peers"),
    ("ASIANPAINT", "BERGEPAINT", "Decorative Paints"),
    ("SBILIFE", "HDFCLIFE", "Private Life Insurers"),
    ("HDFCLIFE", "ICICIPRULI", "Private Life Insurers"),
    ("SUNPHARMA", "DRREDDY", "US/India Large Pharma"),
    ("CIPLA", "LUPIN", "Respiratory & US Generic Pharma"),
    ("HINDUNILVR", "DABUR", "Staples & Personal Care FMCG"),
    ("BRITANNIA", "NESTLEIND", "Packaged Foods FMCG"),
]

def slope(x, y):
    center = stats.fmean(x)
    y_mean = stats.fmean(y)
    dx = [v-center for v in x]
    denom = math.fsum(v*v for v in dx)
    if denom < 1e-18:
        raise ValueError("Too little independent variation to estimate the relationship.")
    return math.fsum(v*(w-y_mean) for v,w in zip(dx,y)) / denom


def residual_adf(s):
    """Fixed-lag EG residual test. Partial out lagged change, without an intercept."""
    delta = [b-a for a,b in zip(s,s[1:])]
    lag = delta[:-1]
    level = s[1:-1]
    response = delta[1:]
    norm = math.fsum(x*x for x in lag)
    if norm < 1e-18:
        raise ValueError("Residual variance is too small.")
    projection = math.fsum(x*y for x,y in zip(lag,level))/norm
    independent = [y-projection*x for x,y in zip(lag,level)]
    scale = math.fsum(x*x for x in independent)
    if scale < 1e-18:
        raise ValueError("Singular residual regression.")
    gamma = math.fsum(x*y for x,y in zip(independent,response))/scale
    partial = [y-gamma*x for x,y in zip(level,response)]
    phi = math.fsum(x*y for x,y in zip(lag,partial))/norm
    rss = math.fsum((y-phi*x)**2 for x,y in zip(lag,partial))
    if rss < 1e-18:
        raise ValueError("Degenerate residual regression.")
    return gamma / math.sqrt(rss/(len(response)-2)/scale)


def analyze(a, b, today, source_ok=True):
    def series(bars):
        result = {}
        for bar in bars:
            day, price = bar['date'], bar['adjclose']
            date.fromisoformat(day)
            if day in result or not isinstance(price,(int,float)) or not math.isfinite(price) or price <= 0:
                raise ValueError("Invalid or duplicate adjusted close.")
            result[day] = price
        return result
    first, second = series(a), series(b)
    dates = sorted(first.keys() & second.keys())[-251:]
    if len(dates) < 121:
        raise ValueError("At least 121 common completed closes are needed.")
    x = [math.log(second[d]) for d in dates]
    y = [math.log(first[d]) for d in dates]
    beta = slope(x[:-1], y[:-1])
    alpha = stats.fmean(y[:-1])-beta*stats.fmean(x[:-1])
    spread = [v-alpha-beta*u for u,v in zip(x,y)]
    train = spread[:-1]
    t = residual_adf(train)
    n = len(train)-1
    critical = -3.33613-6.1101/n-6.823/n**2
    rho = slope(train[:-1],train[1:])
    half = -math.log(2)/math.log(rho) if 0 < rho < 1 else None
    window = max(20,min(60,round(2.5*half))) if half else 40
    reference = train[-window:]
    sd = stats.stdev(reference)
    if sd < 1e-9:
        raise ValueError("Too little spread variation to measure a divergence.")
    mean = stats.fmean(reference)
    z = (spread[-1]-mean)/sd
    age = (today-date.fromisoformat(dates[-1])).days
    reasons = []
    if not source_ok or not 0 <= age <= 4 or dates[-1] != max(first) or dates[-1] != max(second):
        reasons.append("Price history is stale or the latest closes do not align.")
    if beta <= 0 or t >= critical or half is None or not 2.5 <= half <= 45:
        reasons.append("The positive hedge, 5% residual test and half-life requirements did not all pass.")
    if abs(z) >= 3.5:
        reasons.append("Extreme divergence: possible relationship break.")
    direction = ('A' if z < 0 else 'B') if not reasons and abs(z) >= 2 else None
    market_ok = source_ok and 0 <= age <= 4 and dates[-1] == max(first) == max(second)
    equilibrium = ((stats.fmean(train[1:])-rho*stats.fmean(train[:-1]))/(1-rho)
                   if half is not None else None)
    edge = ((equilibrium-spread[-1])*(1-rho**5)*10000/(1+beta)
            if equilibrium is not None and beta > 0 else None)
    if direction and (edge is None or edge == 0 or (edge > 0) != (direction == 'A')):
        reasons.append("Five-session forecast disagrees with rolling spread direction.")
        direction = None
    return dict(market_ok=market_ok,edge_bps=edge,dates=dates, first=[100*first[d]/first[dates[0]] for d in dates],
                second=[100*second[d]/second[dates[0]] for d in dates], spread=spread,
                mean=mean, sd=sd, z=z, beta=beta, alpha=alpha, t=t, critical=critical,
                half=half, window=window, reasons=reasons, direction=direction)


CATALOG = {a+'/'+b:(a,b,label) for a,b,label in CURATED_PAIRS}


def inspect(key):
    a,b,_ = CATALOG[key]
    bars = [prices.history(sym,rng='2y') for sym in (a,b)]
    fresh = all(prices.history_status(sym,rng='2y')['state']=='fresh' for sym in (a,b))
    result = analyze(*bars,config.now_ist().date(),fresh)
    session = (storage.read_json(config.DATA/'current_price_status.json') or {}).get('nse_date')
    if session and result['dates'][-1] < session:
        result["market_ok"] = False
        result['reasons'].append(f"Latest common close precedes the saved NSE session {session}.")
        result['direction'] = None
    if result['direction']:
        cheap, rich = (a,b) if result['direction']=='A' else (b,a)
        score, _ = recommendations.analyse(cheap)
        status, why = recommendations.assess(False, True, score)
        if status != 'buy':
            result['reasons'].append(f"{cheap} entry screen: {why}")
        if any(row['symbol']==cheap for row in tracker.list_events(['dip_confirmed'])):
            result['reasons'].append(f"{cheap} has an unresolved abnormal drop.")
        if result['reasons']:
            result['direction'] = None
        else:
            result.update(cheap=cheap,rich=rich,matches=score['matches'])
    return result


def long_position(key, symbol):
    try:
        r = inspect(key)
    except Exception as exc:
        return 'review', f'pair data unavailable ({type(exc).__name__})'
    if not r['market_ok']:
        return 'review', 'latest paired close unavailable'
    z = r['z'] if symbol == CATALOG[key][0] else -r['z']
    if r['beta'] <= 0 or r['t'] >= r['critical'] or r['half'] is None or not 2.5 <= r['half'] <= 45:
        return 'review', 'relationship no longer qualifies'
    if z <= -3.5:
        return 'sell', f'adverse divergence reached risk limit (long-leg z={z:+.2f})'
    if z >= -0.5:
        return 'sell', f'long leg reached or crossed convergence band (z={z:+.2f})'
    return 'hold', f'awaiting convergence (long-leg z={z:+.2f})'


WATCH = "CREATE TABLE IF NOT EXISTS pair_watch(pair TEXT PRIMARY KEY, session TEXT, direction TEXT)"


def watched():
    with db.connect() as conn:
        conn.execute(WATCH)
        return [row[0] for row in conn.execute("SELECT pair FROM pair_watch ORDER BY pair")]


def watch(key, enabled):
    if key not in CATALOG or type(enabled) is not bool:
        raise ValueError("Choose a listed pair and an alert preference.")
    with db.connect() as conn:
        conn.execute(WATCH)
        conn.execute("INSERT OR IGNORE INTO pair_watch(pair) VALUES(?)" if enabled else
                     "DELETE FROM pair_watch WHERE pair=?", (key,))
    return {'enabled':enabled}


def poll():
    errors = []
    for key in watched():
        try:
            result = inspect(key)
            day, direction = result['dates'][-1], result['direction']
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT session,direction FROM pair_watch WHERE pair=?", (key,)).fetchone()
                if not row or (row[0] and row[0] >= day):
                    continue
                if direction and direction != row[1]:
                    title = f"Pairs model · buy review {result['cheap']} / sell review {result['rich']}"
                    body = (f"Relative-value comparison {key}, completed close {day}. "
                            f"z={result['z']:+.3f} (entry 2 ≤ |z| < 3.5), log hedge β={result['beta']:.3f}. "
                            f"Five-session gross convergence score {abs(result.get('edge_bps') or 0):.1f} bp. "
                            f"Residual ADF={result['t']:.3f}, 5% cutoff {result['critical']:.3f}; "
                            f"half-life {result['half']:.1f} sessions. Cheaper leg matches "
                            f"{', '.join(result['matches'])} with no open confirmed dip. Paired legs only.")
                    outbox.enqueue(title,body,f"pair:{key}:{day}:{direction}",db=conn)
                conn.execute("UPDATE pair_watch SET session=?,direction=? WHERE pair=?", (day,direction,key))
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    if errors:
        raise ValueError('; '.join(errors))
