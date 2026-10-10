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

from . import config, fundamentals, net, prices, storage, universe, workbook

NSE_ARCHIVE = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
MAX_ARCHIVE_MEMBER = 30_000_000
STATUS_PATH = config.DATA / "current_price_status.json"


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
    """{symbol, price, date} rows for EQ-series stocks. Every row must carry the expected trade date."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    headers = reader.fieldnames or []
    sym_col, px_col = _find(headers, SYMBOL_KEYS), _find(headers, PRICE_KEYS)
    ser_col, date_col = _find(headers, SERIES_KEYS), _find(headers, DATE_KEYS)
    if not (sym_col and px_col and ser_col and date_col):
        raise ValueError(f"unexpected bhavcopy columns: {headers[:12]}")
    rows, seen = [], {}
    for raw in reader:
        if str(raw.get(ser_col, "")).strip().upper() != "EQ":
            continue
        sym = str(raw.get(sym_col, "")).strip().upper()
        price = _number(raw.get(px_col))
        if not sym or not universe.SYMBOL_RE.match(sym) or price is None:
            continue
        day = _date(raw.get(date_col))
        if day != expected_date:
            raise ValueError(f"bhavcopy row for {sym} is dated {day}, expected {expected_date}")
        if sym in seen:
            if seen[sym] != price:
                raise ValueError(f"conflicting duplicate prices for {sym}")
            continue
        seen[sym] = price
        rows.append({"symbol": sym, "price": price, "date": day})
    if len(rows) < 500:   # the real file has ~2,000 EQ rows; far fewer means a truncated/wrong file
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
    """Weekdays to try, newest first. Before 17:00 IST today's file isn't expected yet."""
    now = now or config.now_ist()
    start = now.date() - dt.timedelta(days=1 if now.time() < dt.time(17, 0) else 0)
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
    raise ValueError(f"no usable NSE bhavcopy in the last 10 days: {last_error}")


def apply(rows, source, symbols):
    by = {r["symbol"]: r for r in rows}
    updated, stale, missing, no_shares = [], [], [], []
    for sym in symbols:
        row = by.get(sym)
        if not row:
            missing.append(sym)
            continue
        res = workbook.update_current_price(sym, row["price"], row["date"], source=source)
        (updated if res is True else stale if res == "stale" else no_shares).append(sym)
    return {"updated": updated, "stale_not_overwritten": stale, "missing": missing, "no_share_count": no_shares}


def _yahoo_current(sym, cfg):
    bars = prices.history(sym, cfg=cfg)
    if not bars:
        raise ValueError("no Yahoo bars")
    return workbook.update_current_price(sym, bars[-1]["close"], bars[-1]["date"], source="Yahoo close")


def refresh(symbols=None, cfg=None, benchmark_date=None):
    """NSE bulk first; Yahoo for anything NSE missed or when NSE's file lags the benchmark's last close."""
    cfg = cfg or config.load_config()
    symbols = sorted(symbols or fundamentals.all_symbols())
    out = {"at": config.now_ist().isoformat(), "nse_date": None, "nse_error": None}
    try:
        rows, url = fetch_nse()
        out.update(nse_date=rows[0]["date"], nse_url=url, **apply(rows, "NSE bhavcopy close", symbols))
        fallback = out["missing"]
        if benchmark_date and rows[0]["date"] < benchmark_date:
            out["nse_error"] = f"NSE file is for {rows[0]['date']} but Nifty already has a {benchmark_date} close"
            fallback = symbols
    except Exception as exc:
        out.update(nse_error=f"{type(exc).__name__}: {exc}", updated=[], stale_not_overwritten=[],
                   missing=[], no_share_count=[])
        fallback = symbols
    out["yahoo_updated"], out["yahoo_failed"] = [], {}
    for sym in fallback:
        try:
            if _yahoo_current(sym, cfg) is True:
                out["yahoo_updated"].append(sym)
        except Exception as exc:
            out["yahoo_failed"][sym] = str(exc)[:120]
    storage.write_json(STATUS_PATH, out)
    return out
