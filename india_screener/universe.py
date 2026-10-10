"""NSE equity universe (official EQUITY_L.csv) and company-name helpers."""
import csv
import datetime as dt
import io
import re

from . import config, net, storage

NSE_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&.\-]{0,29}$")
CACHE_DAYS = 7
_SUFFIXES = {"limited", "ltd", "ltd.", "company", "co", "co.", "corporation", "corp", "corp.", "inc", "inc."}


def parse_equity_csv(content: bytes):
    rows = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    out = {}
    for raw in rows:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        sym = row.get("SYMBOL", "")
        if row.get("SERIES") == "EQ" and SYMBOL_RE.match(sym):
            out[sym] = {"symbol": sym, "name": row.get("NAME OF COMPANY", sym),
                        "isin": row.get("ISIN NUMBER", ""), "exchange": "NSE"}
    return list(out.values())


def load(refresh=False):
    path = config.CACHE / "nse_universe.json"
    cached = storage.read_json(path)
    if cached and not refresh:
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(cached["fetched_at"])
        if age.days < CACHE_DAYS:
            return cached["records"]
    try:
        records = parse_equity_csv(net.fetch(NSE_EQUITY_URL, timeout=30))
        if len(records) < 500:
            raise ValueError(f"unexpectedly small NSE universe ({len(records)} rows)")
        storage.write_json(path, {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "records": records})
        return records
    except Exception:
        if cached:
            return cached["records"]   # stale but real; caller can inspect cache age
        raise


def lookup(symbol):
    for rec in load():
        if rec["symbol"] == symbol:
            return rec
    return None


def short_name(name: str) -> str:
    """'The Indian Hotels Company Limited' -> 'Indian Hotels'; 'Coal India Limited' -> 'Coal India'."""
    words = re.sub(r"\s+", " ", name.replace("(", " ").replace(")", " ")).strip().split(" ")
    if words and words[0].lower() == "the" and len(words) > 1:
        words = words[1:]
    while len(words) > 1 and words[-1].lower().rstrip(",") in _SUFFIXES:
        words.pop()
    return " ".join(words).rstrip(",")
