"""Daily current prices for every imported company from ONE official NSE file.

Source: NSE's CM-UDiFF Common Bhavcopy Final (end-of-day closing prices for every NSE security),
published after market close at a date-stamped archive URL. One download refreshes price, market cap
and therefore P/E, P/B and margin of safety for all ~250 companies, instead of ~250 Yahoo requests.

Only the *current* valuation price is touched here; the year-end price history used by the
retained-earnings test still comes from Yahoo (workbook.fill_prices, via autofill's weekly job).
If NSE fails, lacks a stock, or its latest file is older than the latest completed Nifty close,
Yahoo fills the gap for those stocks, so a bad NSE day never leaves prices silently stale.

Adapted from the NSE bulk-price module of the user's other copy of this program.
"""
import csv
import datetime as dt
import io
import math
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from . import config, fundamentals, index250, net, prices, storage, universe, workbook

NSE_ARCHIVE = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
MAX_ARCHIVE_MEMBER = 30_000_000
_REFRESH_LOCK = Lock()


def status():
    return storage.read_json(config.DATA / "current_price_status.json") or {}


def official_closes():
    return storage.read_json(config.DATA / "nse_closes.json") or {}


def saved_close(symbol, exchange, official=None):
    """Closing price from the bulk update, when available for this exchange."""
    if exchange.upper() != "NSE":
        return None
    meta = (fundamentals.load(symbol) or {}).get("meta", {})
    source = meta.get("price_source") or ""
    saved = None
    if source.startswith(("NSE bhavcopy close", "Yahoo close")) and meta.get("price_date") and meta.get("price"):
        saved = {"close": meta["price"], "date": meta["price_date"], "source": source.split(" x shares")[0]}
    archived = (official or {}).get(symbol)
    if archived and (not saved or archived["date"] > saved["date"]):
        return archived
    return saved


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


SYMBOL_KEYS = [_key(x) for x in ("TckrSymb", "symbol", "ticker")]
SERIES_KEYS = [_key(x) for x in ("SctySrs", "series")]
PRICE_KEYS = [_key(x) for x in ("ClsPric", "close", "close price", "closing price")]
DATE_KEYS = [_key(x) for x in ("TradDt", "date", "trade date")]


def _find(headers, candidates):
    by = {_key(h): h for h in headers if h is not None}
    return next((by[c] for c in candidates if c in by), None)


def _number(value):
    try:
        x = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def _date(value):
    s = str(value or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%d-%b-%Y", 11), ("%d-%m-%Y", 10), ("%Y%m%d", 8)):
        try:
            return dt.datetime.strptime(s[:n], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_csv(data, expected_date):
    """Official equity closes. Prefer EQ when both EQ and BE are present."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    headers = reader.fieldnames or []
    sym_col, px_col = _find(headers, SYMBOL_KEYS), _find(headers, PRICE_KEYS)
    ser_col, date_col = _find(headers, SERIES_KEYS), _find(headers, DATE_KEYS)
    if not (sym_col and px_col and ser_col and date_col):
        raise ValueError(f"unexpected bhavcopy columns: {headers[:12]}")
    seen = {}
    for raw in reader:
        series = str(raw.get(ser_col, "")).strip().upper()
        if series not in ("EQ", "BE"):
            continue
        sym = str(raw.get(sym_col, "")).strip().upper()
        price = _number(raw.get(px_col))
        if not sym or not universe.SYMBOL_RE.match(sym) or price is None:
            continue
        day = _date(raw.get(date_col))
        if day != expected_date:
            raise ValueError(f"bhavcopy row for {sym} is dated {day}, expected {expected_date}")
        row = {"symbol": sym, "price": price, "date": day, "series": series,
               "isin": (raw.get("ISIN") or "").strip().upper()}
        prior = seen.get(sym)
        if prior and prior["series"] == series and prior != row:
            raise ValueError(f"conflicting duplicate prices for {sym}")
        if prior is None or series == "EQ":
            seen[sym] = row
    rows = list(seen.values())
    if sum(r["series"] == "EQ" for r in rows) < 500:   # the real file has ~2,000 EQ rows; far fewer means a truncated/wrong file
        raise ValueError(f"only {len(rows)} EQ rows in bhavcopy; refusing to use it")
    return rows


def parse_zip(data, expected_date):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            files = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".csv")]
            if len(files) != 1:
                raise ValueError(f"bhavcopy ZIP should hold exactly one CSV (found {len(files)})")
            if files[0].file_size > MAX_ARCHIVE_MEMBER:
                raise ValueError("bhavcopy CSV is too large")
            return parse_csv(zf.read(files[0]), expected_date)
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid bhavcopy ZIP") from exc


def candidate_dates(now=None, days=10):
    """Weekdays to try, newest first. Before the end-of-day cutoff, skip today's file."""
    now = now or config.now_ist()
    start = now.date() - dt.timedelta(days=1 if now.hour < config.EOD_READY_HOUR else 0)
    return [d for d in (start - dt.timedelta(days=i) for i in range(days)) if d.weekday() < 5]


def fetch_nse():
    """(rows, url) for the latest published final bhavcopy, walking back over holidays."""
    last_error = None
    for day in candidate_dates():
        url = NSE_ARCHIVE.format(date=day.strftime("%Y%m%d"))
        try:
            raw = net.fetch(url, timeout=30, retries=0, headers={"Referer": "https://www.nseindia.com/"})
            return parse_zip(raw, day.isoformat()), url
        except (net.FetchError, ValueError, UnicodeError) as exc:
            last_error = exc   # a holiday has no file: try the day before
            if isinstance(exc, net.FetchError) and "HTTP 404" not in str(exc) and "HTTP 403" not in str(exc):
                break  # a network outage is not specific to one date
    raise ValueError(f"no usable NSE bhavcopy in the last 10 days: {last_error}")


def nse_by_symbol(rows):
    by = {r["symbol"]: r for r in rows}
    ids = {r["symbol"]: r.get("isin") for r in workbook.mcap_universe()}
    ids.update({sym: r.get("isin") for sym, r in index250.by_symbol().items()})
    by_isin = {}
    for row in rows:
        if row.get("isin"):
            by_isin.setdefault(row["isin"], []).append(row)
    for sym, isin in ids.items():
        row = by.get(sym)
        if isin and (not row or row.get("isin") and row["isin"] != isin):
            matches = by_isin.get(isin, [])
            if len(matches) == 1:
                by[sym] = matches[0]
            else:
                by.pop(sym, None)
    return by


def _source(row, symbol, source):
    if row["symbol"] != symbol:
        return f"{source} ({row['symbol']}, same ISIN)"
    if row.get("series") == "BE":
        return f"{source} (BE series)"
    return source


def apply(rows, source, symbols):
    by = nse_by_symbol(rows)
    updated, stale, missing, no_shares = [], [], [], []
    for sym in symbols:
        row = by.get(sym)
        if not row:
            missing.append(sym)
            continue
        res = workbook.update_current_price(sym, row["price"], row["date"], source=_source(row, sym, source),
                                            keep_price_without_shares=True)
        (updated if res is True else stale if res == "stale" else no_shares).append(sym)
    return {"updated": updated, "stale_not_overwritten": stale, "missing": missing, "no_share_count": no_shares}


def _yahoo_current(sym, cfg, target):
    bars = prices.history(sym, rng="5d", cfg=cfg)
    if target and bars[-1]["date"] < target:
        bars = prices.history(sym, rng="5d", force=True, cfg=cfg)
    last = bars[-1]
    if target and last["date"] < target:
        raise ValueError(f"Yahoo has only {last['date']}; latest known session is {target}")
    return workbook.update_current_price(sym, last["close"], last["date"], source="Yahoo close", keep_price_without_shares=True)


def refresh(symbols=None, cfg=None, benchmark_date=None):
    """Refresh closes and valuation inputs, serializing bulk refreshes in this process."""
    with _REFRESH_LOCK:
        return _refresh(symbols, cfg or config.load_config(), benchmark_date)


def _refresh(symbols, cfg, benchmark_date):
    symbols = sorted(fundamentals.all_symbols() if symbols is None else symbols)
    previous = status()
    out = {"at": config.now_ist().isoformat(), "nse_date": None, "nse_error": None}
    try:
        rows, url = fetch_nse()
        old_archive = official_closes()
        old_day = max((row["date"] for row in old_archive.values()), default="")
        if rows[0]["date"] >= old_day:
            archive = {sym: {"close": row["price"], "date": row["date"],
                        "source": _source(row, sym, "NSE bhavcopy close")}
                       for sym, row in nse_by_symbol(rows).items()}
            storage.write_json(config.DATA / "nse_closes.json", archive)
        out.update(nse_date=rows[0]["date"], nse_url=url, **apply(rows, "NSE bhavcopy close", symbols))
    except Exception as exc:
        out.update(nse_error=f"{type(exc).__name__}: {exc}", updated=[], stale_not_overwritten=[],
                   missing=[], no_share_count=[])

    # Check the benchmark only if the exchange archive might be lagging.
    candidates = candidate_dates()
    if not benchmark_date and (not out["nse_date"] or
            (candidates and out["nse_date"] < candidates[0].isoformat())):
        for key in ("benchmark", "benchmark_fallback"):
            try:
                benchmark_date = prices.history(cfg[key], rng="5d", force=True, cfg=cfg)[-1]["date"]
                break
            except (ValueError, net.FetchError, prices.DataError):
                continue
    target = max(filter(None, (out["nse_date"], benchmark_date, previous.get("session"), previous.get("nse_date"))),
                 default=None)
    fallback = out["missing"]
    if out["nse_date"] != target or not out["nse_date"]:
        if out["nse_date"] and target:
            out["nse_error"] = f"NSE file is for {out['nse_date']} but Nifty or saved data has a {target} close"
        fallback = symbols

    def update(sym):
        try:
            saved = saved_close(sym, "NSE")
            if target and saved and saved["date"] >= target:
                return sym, "stale"
            return sym, _yahoo_current(sym, cfg, target)
        except Exception as exc:
            return sym, str(exc)[:120]

    out["yahoo_updated"], out["yahoo_failed"] = [], {}
    if fallback:
        with ThreadPoolExecutor(max_workers=min(4, max(1, int(cfg["workers"])))) as pool:
            for sym, result in pool.map(update, fallback):
                if result is True:
                    out["yahoo_updated"].append(sym)
                elif result is False:
                    if sym not in out["no_share_count"]:
                        out["no_share_count"].append(sym)
                elif result != "stale":
                    out["yahoo_failed"][sym] = result
    dates = [(saved_close(sym, "NSE") or {}).get("date") for sym in symbols]
    out["session"] = max(filter(None, [target, *dates]), default=None)
    out["total"] = len(symbols)
    out["current_count"] = sum(day is not None and day == out["session"] for day in dates)
    storage.write_json(config.DATA / "current_price_status.json", out)
    return out
