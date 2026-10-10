"""The hand-off point between your Buffett/Munger/Lynch scoring core and the daily news/event tracker.

data/shortlist.json contract (the scoring core overwrites this file each quarter):
{
  "generated_at": "2026-09-30T18:00:00+05:30",
  "source": "quarterly rescreen",
  "stocks": [
    {"symbol": "INFY", "exchange": "NSE", "name": "Infosys Limited",
     "aliases": ["Infosys"],                                   # optional extra news search terms
     "scores": {"buffett": 81.2, "munger": 77.0, "lynch": 64.5}}  # optional, shown in reports
  ]
}
Current holdings from the ledger are always tracked too, whether or not they are shortlisted.
"""
import datetime as dt

from . import config, storage
from .universe import SYMBOL_RE


def load():
    data = storage.read_json(config.SHORTLIST_PATH) or {"stocks": []}
    return data


def save(stocks, source="manual"):
    for s in stocks:
        if not SYMBOL_RE.match(s["symbol"]):
            raise ValueError(f"invalid symbol {s['symbol']!r}")
    storage.write_json(config.SHORTLIST_PATH, {"generated_at": config.now_ist().isoformat(),
                                               "source": source, "stocks": stocks})


def add(symbol, exchange="NSE", name=None, aliases=()):
    data = load()
    stocks = [s for s in data["stocks"] if s["symbol"] != symbol]
    entry = {"symbol": symbol, "exchange": exchange}
    if name:
        entry["name"] = name
    if aliases:
        entry["aliases"] = list(aliases)
    stocks.append(entry)
    save(stocks, data.get("source", "manual"))


def remove(symbol):
    data = load()
    stocks = [s for s in data["stocks"] if s["symbol"] != symbol]
    save(stocks, data.get("source", "manual"))
    return len(data["stocks"]) - len(stocks)
