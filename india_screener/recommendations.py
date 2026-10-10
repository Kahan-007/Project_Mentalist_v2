"""Permanent, append-only record of every recommendation-type moment, with the full analysis at that time.

A row is written when a stock is shortlisted or removed, when an alert goes out, and when you buy or sell.
Each row freezes what the program knew then: price and its date, Buffett/Munger/Lynch scores and matches,
every scoring criterion with its value, recent headlines, and the alert text. Later re-imports or price
changes never alter old rows, so you can always look back and see *why* something was flagged, and judge
the program's calls against what actually happened.

The rows live in data/screener.sqlite (table recommendation_log); `py -m india_screener ledger` prints them
and `py -m india_screener ledger --csv FILE` exports them for Excel.
"""
import csv
import io
import json
import math

from . import config, db as dbm, fundamentals, portfolio, shortlist, storage

COLUMNS = ("logged_at", "symbol", "reason", "held", "shortlisted", "price", "price_date", "price_source",
           "buffett", "munger", "lynch", "matches", "note")


def signal(held, shortlisted, matches, overvalued=False):
    """The dashboard's colour code. 'sell': you hold it and it matches no philosophy, or its price is above the
    price-discipline limit. 'hold': held, still matches, price within the limit. 'buy': shortlisted, not held,
    matches at least one, price within the limit. 'watch': shortlisted but not matching, or too expensive.
    None: neither held nor shortlisted."""
    if held:
        return "hold" if matches and not overvalued else "sell"
    if shortlisted:
        return "buy" if matches and not overvalued else "watch"
    return None


def assess(held, shortlisted, score, st=None):
    """(signal, reason) using the scores plus the price-discipline setting."""
    from . import scoring
    st = st or scoring.settings()
    matches = (score or {}).get("matches") or []
    val = (score or {}).get("valuation") or {}
    lim, mult = st.get("price_discipline") or 0, val.get("multiple")
    over = bool(lim and mult and mult > lim)
    s = signal(held, shortlisted, matches, over)
    if s is None:
        return None, ""
    price_txt = (f"price is {mult:.1f}x the estimated value ({val.get('method')})" if mult
                 else "no positive value estimate (e.g. negative free cash flow), so price discipline can't be checked")
    names = ", ".join(k.title() for k in matches)
    if s == "sell":
        why = (f"still matches {names}, but {price_txt}, above your {lim:g}x limit" if matches
               else "matches none of the three philosophies")
    elif s == "watch":
        why = (f"matches {names}, but {price_txt}, above your {lim:g}x limit" if matches
               else "doesn't match any philosophy right now")
    else:
        why = f"matches {names}; {price_txt}" + (f" (limit {lim:g}x)" if lim else "")
    return s, why


def _clean(x):
    """JSON-safe copy (NaN/inf become null) so every analysis snapshot can be stored and read back."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def analyse(symbol):
    """(analysis dict or None, error text or None) using the same scoring the dashboard shows."""
    rec = fundamentals.load(symbol)
    if not rec:
        return None, "financials not imported"
    try:
        from . import server
        return server._score(rec), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _recent_headlines(symbol, n=5, path=None):
    with dbm.connect(path) as db:
        return [dict(r) for r in db.execute(
            "SELECT title,source,published_utc,url FROM news WHERE symbol=? ORDER BY published_utc DESC LIMIT ?",
            (symbol, n))]


def record(symbol, reason, note="", headlines=None, path=None):
    symbol = symbol.upper()
    analysis, err = analyse(symbol)
    meta = (fundamentals.load(symbol) or {}).get("meta") or {}
    held = symbol in portfolio.holdings(path)
    listed = symbol in {s["symbol"] for s in shortlist.load().get("stocks", [])}
    heads = headlines if headlines is not None else _recent_headlines(symbol, path=path)
    sc = lambda k: (analysis or {}).get(k, {}).get("score")
    with dbm.connect(path) as db:
        cur = db.execute(
            "INSERT INTO recommendation_log(logged_at,symbol,reason,held,shortlisted,price,price_date,price_source,"
            "buffett,munger,lynch,matches,note,headlines,analysis) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (config.now_ist().isoformat(timespec="seconds"), symbol, reason, int(held), int(listed),
             meta.get("price"), meta.get("price_date") or meta.get("market_cap_date"), meta.get("price_source"),
             sc("buffett"), sc("munger"), sc("lynch"), json.dumps((analysis or {}).get("matches", [])),
             note if not err else (note + ("\n" if note else "") + f"[analysis unavailable: {err}]"),
             json.dumps(_clean(heads)), json.dumps(_clean(analysis or {}))))
        return cur.lastrowid


def safe_record(*args, **kw):
    """Logging must never break the action it records (a shortlist add, an alert, a trade)."""
    try:
        return record(*args, **kw)
    except Exception as exc:
        with open(config.LOGS / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"[{config.now_ist().isoformat()}] recommendation log failed for {args}: {exc}\n")
        return None


def log_signal_changes(items, analyses):
    """Record a row whenever a watched stock's Buy / Sell / In-portfolio signal changes (checked daily).
    Stocks whose financials aren't imported have no signal to track and are skipped."""
    path = config.DATA / "signals.json"
    last = storage.read_json(path) or {}
    now = {}
    for it in items:
        r, _ = analyses.get(it["symbol"], (None, None))
        if not r:
            continue
        s, why = assess(it["held"], it["shortlisted"], r)
        now[it["symbol"]] = s
        if s in ("buy", "sell") and last.get(it["symbol"]) != s:
            safe_record(it["symbol"], "signal_" + s,
                        note=f"signal changed from {last.get(it['symbol']) or 'none'} to {s}: {why}")
    storage.write_json(path, now)


def history(symbol=None, limit=500, full=False, path=None):
    cols = "*" if full else "id," + ",".join(COLUMNS)
    q, args = f"SELECT {cols} FROM recommendation_log", []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol.upper())
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with dbm.connect(path) as db:
        rows = [dict(r) for r in db.execute(q, args)]
    for r in rows:
        for k in ("matches", "headlines", "analysis"):
            if k in r:
                r[k] = json.loads(r[k])
    return rows


def to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([", ".join(r[c]) if c == "matches" else r[c] for c in COLUMNS])
    return buf.getvalue()
