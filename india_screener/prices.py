"""Daily OHLCV from Yahoo Finance's public chart endpoint (unofficial; can change without notice).

Bars: {date, open, high, low, close, adjclose, volume}
  close    = split-adjusted close as Yahoo reports it
  adjclose = split + dividend adjusted (use for returns)
"""
import datetime as dt
import json
import math
from urllib.parse import quote

from . import config, net, storage

CHART = "https://{host}/v8/finance/chart/{sym}?range={rng}&interval={interval}&events=div%2Csplits&includeAdjustedClose=true"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
MAX_BAD_FRACTION = 0.05


class DataError(Exception):
    pass


def yahoo_symbol(symbol, exchange="NSE"):
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return symbol + (".BO" if exchange.upper() == "BSE" else ".NS")


def parse_chart(payload, now=None, expected_symbol=None):
    """Validate and normalise a Yahoo chart payload. Returns (bars, problems)."""
    now = now or config.now_ist()
    result = (payload.get("chart") or {}).get("result")
    if not result:
        err = (payload.get("chart") or {}).get("error")
        raise DataError(f"no instrument data ({err})" if err else "no instrument data")
    obj = result[0]
    meta = obj.get('meta', {})
    if expected_symbol and meta.get('symbol') != expected_symbol:
        raise DataError('quote instrument does not match the requested symbol')
    if obj.get("meta", {}).get("currency") != "INR":
        raise DataError(f"expected INR, got {obj.get('meta', {}).get('currency')}")
    stamps = obj.get("timestamp") or []
    q = obj["indicators"]["quote"][0]
    adj_block = obj["indicators"].get("adjclose")
    if not adj_block:
        raise DataError('adjusted history unavailable; cannot assume no corporate actions')
    adj = adj_block[0]["adjclose"]
    if any(len(q.get(k, [])) != len(stamps) for k in ('open','high','low','close','volume')) or len(adj) != len(stamps):
        raise DataError('incomplete candle arrays')
    today = now.date()
    eod_ready = now.hour >= config.EOD_READY_HOUR
    by_date, problems = {}, []
    placeholders = []
    # Timestamp order is not guaranteed by an upstream cache.
    if any(isinstance(t, bool) or not isinstance(t, (int,float)) or not math.isfinite(t) or t <= 0 for t in stamps):
        raise DataError('invalid candle timestamp')
    for i in sorted(range(len(stamps)), key=lambda i: stamps[i]):
        ts = stamps[i]
        day = dt.datetime.fromtimestamp(ts, config.IST).date()
        if day > today or (day == today and not eod_ready):
            continue   # never use an unfinished candle
        o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
        a, v = adj[i], q["volume"][i]
        vals = (o, h, l, c, a)
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in vals):
            problems.append(f"{day}: missing/invalid price")
            continue
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or v < 0):
            problems.append(f'{day}: invalid volume')
            continue
        if meta.get('instrumentType') == 'EQUITY' and v == 0 and o == h == l == c:
            previous = next(reversed(by_date.values()), None)
            if previous and previous['close'] == c and previous['adjclose'] == a:
                placeholders.append(f'{day}: zero-volume carry-forward placeholder excluded')
                continue
        tol = 1e-4 * c
        if h + tol < max(o, c, l) or l - tol > min(o, c, h):
            problems.append(f"{day}: inconsistent OHLC")
            continue
        bar = {"date": str(day), "open": float(o), "high": float(h), "low": float(l),
                             "close": float(c), "adjclose": float(a),
                             "volume": float(v) if isinstance(v, (int, float)) and v and v > 0 else 0.0}
        if str(day) in by_date and by_date[str(day)] != bar:
            raise DataError(f'{day}: conflicting duplicate daily sessions')
        by_date[str(day)] = bar
    bars = [by_date[d] for d in sorted(by_date)]
    if len(bars) < 2:
        raise DataError("insufficient completed history")
    if len(problems) > MAX_BAD_FRACTION * (len(bars) + len(problems)):
        raise DataError(f"too many bad bars ({len(problems)}); first: {problems[0]}")
    return bars, problems + placeholders


def _cache_key(now):
    today = now.date().isoformat()
    return today if now.hour >= config.EOD_READY_HOUR else today + "-pre"


def history(symbol, exchange="NSE", rng=None, force=False, cfg=None):
    cfg = cfg or config.load_config()
    rng = rng or cfg["history_range"]
    ysym = yahoo_symbol(symbol, exchange)
    now = config.now_ist()
    path = config.CACHE / "prices" / (ysym.replace("^", "IDX_") + f"_{rng}.json.gz")
    cached = storage.read_json(path)
    if cached and not force and cached.get("key") == _cache_key(now):
        return cached["bars"]
    last = None
    for host in HOSTS:
        try:
            raw = net.fetch(CHART.format(host=host, sym=quote(ysym, safe=""), rng=rng, interval="1d"))
            bars, problems = parse_chart(json.loads(raw), now, expected_symbol=ysym)
            storage.write_json(path, {"symbol": ysym, "key": _cache_key(now), "fetched_at": now.isoformat(),
                                      "problems": problems[:50], "bars": bars})
            storage.write_json(path.with_suffix('.status.json'), {'state':'fresh', 'checked_at':now.isoformat(),
                               'last_session':bars[-1]['date'], 'problems':problems[:50]})
            return bars
        except net.FetchError as exc:
            last = exc
            if "HTTP 404" in str(exc) or "HTTP 400" in str(exc):
                break
        except (DataError, ValueError, KeyError, IndexError) as exc:
            last = exc
    if cached:   # stale real data beats no data; caller sees the dates
        storage.write_json(path.with_suffix('.status.json'), {'state':'stale fallback', 'checked_at':now.isoformat(),
                           'last_session':cached['bars'][-1]['date'], 'error':str(last)[:200]})
        return cached["bars"]
    raise DataError(f"{ysym}: {last}")


def history_status(symbol, exchange='NSE', rng=None):
    rng = rng or config.load_config()['history_range']
    ysym = yahoo_symbol(symbol, exchange).replace('^', 'IDX_')
    return storage.read_json((config.CACHE/'prices'/f'{ysym}_{rng}.json.gz').with_suffix('.status.json')) or {'state':'unknown'}


def benchmark(cfg=None):
    cfg = cfg or config.load_config()
    try:
        return history(cfg["benchmark"], cfg=cfg)
    except Exception:
        return history(cfg["benchmark_fallback"], cfg=cfg)


def quote_now(symbol, exchange="NSE"):
    """Indicative (possibly delayed) last price. Not used for any calculation that needs closes."""
    ysym = yahoo_symbol(symbol, exchange)
    raw = net.fetch(CHART.format(host=HOSTS[0], sym=quote(ysym, safe=""), rng="1d", interval="5m"), timeout=10, retries=0)
    meta = json.loads(raw)["chart"]["result"][0]["meta"]
    price, prev, ts = meta.get("regularMarketPrice"), meta.get("chartPreviousClose"), meta.get("regularMarketTime")
    if (meta.get("currency") != "INR" or meta.get('symbol') != ysym or
            not all(not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (price, prev, ts))):
        raise DataError(f"{ysym}: incomplete quote")
    return {"symbol": symbol, "price": float(price), "previous_close": float(prev),
            "time": dt.datetime.fromtimestamp(ts, config.IST).isoformat()}
