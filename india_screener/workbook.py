"""Import the combined 'NSE_Top250_Financials' workbook (one flat table of Screener annual line items for
250 companies) into per-company fundamentals records the scoring engine understands.

What it provides vs a single-company Screener export:
  + all 250 companies at once, reported capex, owners' profit, market-cap rank, basis per company
  - no year-end share prices (fill them from Yahoo with fill_prices), no quarterly figures (P/E uses the
    last financial year), no share-count row (derived exactly as Profit for EPS / EPS, which Screener
    adjusts for splits and bonuses; verified against the 'Adjusted Equity Shares' of real exports).
Non-12-month transition periods (e.g. 'Mar 2024 15m') have their profit and cash-flow items annualised.
"""
import calendar
import datetime as dt
import io
import math
import re

from . import config, fundamentals, index250, storage, xlsx, financial_math

YEARS = [f"FY{y}" for y in range(2017, 2027)]
FLOW_KEYS = {"sales", "other_income", "depreciation", "interest", "pbt", "tax", "net_profit", "dividend",
             "cfo", "cfi", "cff", "net_cash_flow", "capex_reported", "profit_for_eps", "associates", "minority"}
ITEMS = {
    "Sales / Revenue": "sales", "Other Income": "other_income", "Depreciation": "depreciation",
    "Interest / Finance Cost": "interest", "Profit Before Tax": "pbt", "Tax": "tax",
    "Net Profit": "_np_total", "Net Profit attributable to owners": "_np_owners",
    "Profit for EPS": "profit_for_eps", "EPS in Rs": "_eps", "Dividend Payout %": "_payout",
    "Equity Capital": "share_capital", "Reserves": "reserves", "Borrowings": "borrowings",
    "Other Liabilities": "other_liabilities", "Total equity and liabilities": "total", "Total Assets": "total_assets",
    "Fixed Assets (net, including intangibles)": "net_block", "CWIP": "cwip", "Investments": "investments",
    "Other Assets": "other_assets", "Trade Receivables": "receivables", "Inventory": "inventory",
    "Cash & Equivalents": "cash", "Cash from Operations": "cfo", "Cash from Investing Activity": "cfi",
    "Cash from Financing Activity": "cff", "Net Cash Flow": "net_cash_flow", "Capital Expenditure": "capex_reported",
    "Profit from Associates": "associates", "Minority share": "minority",
}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
SCREENER_BANK = re.compile(r"\bbank\b", re.I)
SCREENER_INS = re.compile(r"insurance", re.I)
SCREENER_FEE = re.compile(r"asset management|exchange|depositor|stockbroking|rating", re.I)


def num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace(",", "")
    if s in ("", "nan"):
        return None
    if s in ("-", "—"):
        return 0.0      # the workbook shows source zeros as a dash
    neg = s.startswith("(") and s.endswith(")")
    try:
        x = float(s.strip("()"))
    except ValueError:
        return None
    return -x if neg else x


def period(label):
    """'Mar 2024 15m' -> ('2024-03-31', 15). Returns (None, None) for blanks."""
    if not isinstance(label, str) or not label.strip():
        return None, None
    parts = label.split()
    mon, year = MONTHS.get(parts[0][:3].lower()), int(parts[1])
    months = int(parts[2].rstrip("m")) if len(parts) > 2 and parts[2].endswith("m") else 12
    last = calendar.monthrange(year, mon)[1]
    return dt.date(year, mon, last).isoformat(), months


def _table(grid):
    head = [str(h).strip() if h is not None else "" for h in grid[0]]
    return [dict(zip(head, row)) for row in grid[1:] if any(v not in (None, "") for v in row)]


def suggest_type(symbol, industry, basis_policy):
    ix = index250.by_symbol().get(symbol)
    if ix:
        return ix["suggested_type"]
    if symbol in index250.TYPE_OVERRIDES:
        return index250.TYPE_OVERRIDES[symbol]
    ind = industry or ""
    if SCREENER_INS.search(ind):
        return "insurance"
    if SCREENER_BANK.search(ind):
        return "bank"
    if SCREENER_FEE.search(ind):
        return "general"
    if str(basis_policy or "").lower().startswith("standalone"):
        return "nbfc"
    return "general"


def parse(data: bytes):
    sheets = xlsx.read_workbook(io.BytesIO(data))
    need = {"Financials", "Companies", "Periods"}
    if not need <= set(sheets):
        raise fundamentals.ImportError_(f"not the combined financials workbook (needs sheets {sorted(need)}, found {sorted(sheets)})")
    companies = _table(sheets["Companies"])
    fin = _table(sheets["Financials"])
    periods = _table(sheets["Periods"])
    per = {}
    for p in periods:
        if str(p.get("Statement", "")).lower().startswith("income"):
            per[p["NSE Symbol"]] = [period(p.get(y)) for y in YEARS]
    items = {}
    for r in fin:
        key = ITEMS.get(str(r.get("Item", "")).strip())
        if key and r.get("Value Type") != "Unavailable":
            items.setdefault(r["NSE Symbol"], {})[key] = [num(r.get(y)) for y in YEARS]
    return companies, items, per


def build_record(c, it, per_list, retrieved):
    sym = c["NSE Symbol"]
    cols = [i for i, (d, _) in enumerate(per_list) if d and any((it.get(k) or [None] * 10)[i] is not None for k in ("sales", "_np_total"))]
    if len(cols) < 3:
        return None, f"only {len(cols)} year(s) of data"
    years = [per_list[i][0] for i in cols]
    months = [per_list[i][1] for i in cols]
    pick = lambda k: [(it.get(k) or [None] * 10)[i] for i in cols]
    s = {k: pick(k) for k in ITEMS.values() if not k.startswith("_")}
    owners, total, eps, payout = pick("_np_owners"), pick("_np_total"), pick("_eps"), pick("_payout")
    s["net_profit"] = [o if o is not None else t for o, t in zip(owners, total)]
    s["dividend"] = [p * n if p is not None and n is not None else None for p, n in zip(payout, s["profit_for_eps"])]
    s["shares_cr"] = [pf / e if pf is not None and e not in (None, 0) and pf / e > 0 else None
                      for pf, e in zip(s["profit_for_eps"], eps)]
    s["price"] = [None] * len(years)
    notes = []
    for i, mth in enumerate(months):
        if mth and mth != 12:
            f = 12 / mth
            for k in FLOW_KEYS:
                if s.get(k) and s[k][i] is not None:
                    s[k][i] *= f
            notes.append(f"{years[i][:7]} covered {mth} months; profit and cash-flow items annualised")
    shares_now = s["shares_cr"][-1]
    mcap = num(c.get("Market Cap (INR crore)"))
    meta = {"market_cap": mcap, "shares": shares_now * 1e7 if shares_now else None,
            "price": mcap / shares_now if mcap and shares_now else None,
            "market_cap_date": retrieved, "price_source": "market cap / shares (snapshot)"}
    basis = "standalone" if str(c.get("Basis", "")).lower().startswith("standalone") else "consolidated"
    ix = index250.by_symbol().get(sym)
    rec = {"symbol": sym, "name": c.get("Company"), "basis": basis,
           "sector_type": suggest_type(sym, c.get("Industry"), c.get("Basis Policy")),
           "industry": ix["industry"] if ix else c.get("Industry"),
           "screener_industry": c.get("Industry"),
           "isin": c.get("ISIN"), "mcap_rank": int(num(c.get("Market-cap Rank")) or 0) or None,
           "source": f"Top 250 workbook (Screener tables retrieved {retrieved})",
           "years": years, "series": s, "meta": meta, "quarters": {"dates": []},
           "share_provenance":{"historical":"inferred weighted-average adjusted shares from profit-for-EPS / EPS",
                               "current":"latest available weighted-average shares; verify current outstanding shares"},
           "period_notes": notes + ([str(c["Company Notes"])] if c.get("Company Notes") else [])}
    return rec, None


def import_workbook(data: bytes, keep_exports=True, retrieved=None):
    companies, items, per = parse(data)
    # Import time is not the source snapshot date. Undated snapshots remain undated.
    if retrieved is not None:
        retrieved = dt.date.fromisoformat(retrieved).isoformat()
    universe, imported, merged, skipped = [], [], [], []
    for c in companies:
        sym = c.get("NSE Symbol")
        if not sym:
            continue
        universe.append({"symbol": sym, "name": c.get("Company"), "isin": c.get("ISIN"),
                         "rank": int(num(c.get("Market-cap Rank")) or 0), "market_cap_cr": num(c.get("Market Cap (INR crore)")),
                         "industry": c.get("Industry"), "basis": c.get("Basis"),
                         "suggested_type": suggest_type(sym, c.get("Industry"), c.get("Basis Policy"))})
        rec, why = build_record(c, items.get(sym, {}), per.get(sym, [(None, None)] * 10), retrieved)
        if not rec:
            skipped.append({"symbol": sym, "reason": why})
            continue
        existing = fundamentals.load(sym)
        if keep_exports and existing and existing.get("quarters", {}).get("dates"):
            # A Screener export is richer (quarters, year-end prices). Keep it; add what the workbook knows better.
            by_year = dict(zip(rec["years"], rec["series"]["capex_reported"]))
            prior = dict(zip(existing["years"], existing["series"].get("capex_reported", [])))
            existing["series"]["capex_reported"] = [by_year.get(y) if by_year.get(y) is not None else prior.get(y) for y in existing["years"]]
            existing["mcap_rank"] = rec["mcap_rank"]
            existing.setdefault("screener_industry", rec["screener_industry"])
            storage.write_json(config.DATA / "fundamentals" / f"{sym}.json", existing)
            merged.append(sym)
            continue
        if existing and existing["years"][-1] > rec["years"][-1]:
            skipped.append({"symbol":sym, "reason":"stored financial years are newer"})
            continue
        if existing:
            if existing.get("valuation_research"):
                rec["valuation_research"] = existing["valuation_research"]
            rec["manual"] = existing.get("manual", {})
            rec["manual_source"] = existing.get("manual_source", {})
            old_meta, shares = existing.get("meta") or {}, (rec["meta"].get("shares") or 0) / 1e7
            if old_meta.get("price_date") and old_meta["price_date"] >= (retrieved or "") and old_meta.get("price") and shares:
                # keep a fresher daily close (e.g. NSE) instead of the workbook's snapshot
                rec["meta"].update(price=old_meta["price"], market_cap=old_meta["price"] * shares,
                                   market_cap_date=old_meta["price_date"], price_date=old_meta["price_date"],
                                   price_source=old_meta.get("price_source"))
            if existing.get("sector_type_set_by_user"):
                rec["sector_type"] = existing["sector_type"]
                rec["sector_type_set_by_user"] = True
            old_prices = dict(zip(existing.get("years", []), (existing.get("series") or {}).get("price", [])))
            rec["series"]["price"] = [old_prices.get(y) for y in rec["years"]]
        rec.setdefault("manual", {})
        rec["imported_at"] = config.now_ist().isoformat()
        storage.write_json(config.DATA / "fundamentals" / f"{sym}.json", rec)
        imported.append(sym)
    storage.write_json(config.DATA / "universe_mcap250.json", {"retrieved": retrieved, "companies": universe})
    return {"imported": imported, "merged_with_exports": merged, "skipped": skipped, "universe": len(universe)}


def mcap_universe():
    return (storage.read_json(config.DATA / "universe_mcap250.json") or {}).get("companies", [])


def parse_latest_prices(text: str):
    """A simple 'Ticker,Latest_Price' CSV (Yahoo-style .NS/.BO tickers). Returns {symbol: price}."""
    import csv as _csv
    import io as _io
    rows = list(_csv.DictReader(_io.StringIO(text.lstrip("\ufeff"))))
    cols = {c.strip().lower(): c for c in (rows[0].keys() if rows else [])}
    need = {"ticker", "latest_price"}
    if not need <= set(cols):
        raise fundamentals.ImportError_(f"expected columns Ticker, Latest_Price; found {list(cols.values())}")
    out = {}
    for r in rows:
        t = (r.get(cols["ticker"]) or "").strip().upper()
        sym = t.rsplit(".", 1)[0] if t.endswith((".NS", ".BO")) else t
        p = fundamentals._num(r.get(cols["latest_price"]))
        if sym and p and p > 0:
            out[sym] = p
    if len(out) < 10:
        raise fundamentals.ImportError_(f"only {len(out)} usable price rows found; check the file")
    return out


def apply_latest_prices(text: str):
    """Refresh current price and market cap for every already-imported company this file covers.
    A snapshot only: it does not supply the year-end price history the retained-earnings test needs
    (use 'Fill prices from Yahoo' for that)."""
    prices = parse_latest_prices(text)
    today = config.now_ist().date().isoformat()
    updated, skipped = [], []
    for sym, price in prices.items():
        rec = fundamentals.load(sym)
        if not rec:
            continue
        shares, _ = financial_math.current_shares(rec)
        if not shares:
            skipped.append(sym)
            continue
        rec["meta"]["price"] = price
        rec["meta"]["market_cap"] = price * shares
        rec["meta"]["market_cap_date"] = today
        rec["meta"]["price_date"] = today
        rec["meta"]["price_source"] = "latest price snapshot (user-provided)"
        storage.write_json(config.DATA / "fundamentals" / f"{sym}.json", rec)
        updated.append(sym)
    return {"updated": updated, "skipped_no_shares": skipped, "in_file": len(prices),
           "not_yet_imported": len(prices) - len(updated) - len(skipped)}


def update_current_price(symbol, price, as_of, source="current-price snapshot"):
    """Update only the current valuation price and market cap; year-end history is untouched.
    Returns True when updated, "stale" when the record already holds a newer price, False when the
    company has no share count to turn a price into a market cap."""
    rec = fundamentals.load(symbol)
    if not rec:
        return False
    price = float(price)
    day = dt.date.fromisoformat(str(as_of)[:10]).isoformat()
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"{symbol}: current price must be positive")
    if day > config.now_ist().date().isoformat():
        raise ValueError(f"{symbol}: current price date {day} is in the future")
    if str(rec.get("meta", {}).get("price_date") or rec.get("meta", {}).get("market_cap_date") or "")[:10] > day:
        return "stale"
    shares, _ = financial_math.current_shares(rec)
    if not shares:
        return False
    meta = rec.setdefault("meta", {})
    meta.update(price=price, market_cap=price * shares, market_cap_date=day, price_date=day,
                price_source=f"{source} x shares")
    storage.write_json(config.DATA / "fundamentals" / f"{symbol}.json", rec)
    return True


def fill_prices(symbol, bars):
    """Fill year-end prices (for the retained-earnings test) and refresh market cap from Yahoo daily bars."""
    rec = fundamentals.load(symbol)
    if not rec or not bars:
        return False
    closes = {b["date"]: b["close"] for b in bars}
    dates = sorted(closes)
    prices = list((rec["series"].get("price") or [None] * len(rec["years"])))
    for i, y in enumerate(rec["years"]):
        prior = [d for d in dates if d <= y]
        if prior and (dt.date.fromisoformat(y) - dt.date.fromisoformat(prior[-1])).days <= 7:
            prices[i] = closes[prior[-1]]
    rec["series"]["price"] = prices
    shares, _ = financial_math.current_shares(rec)
    newer = str(rec["meta"].get("price_date") or "")[:10] > bars[-1]["date"]   # e.g. today's NSE close
    if shares and not newer:
        rec["meta"]["price_date"] = bars[-1]["date"]
        rec["meta"]["price"] = bars[-1]["close"]
        rec["meta"]["market_cap"] = bars[-1]["close"] * shares
        rec["meta"]["market_cap_date"] = bars[-1]["date"]
        rec["meta"]["price_source"] = "Yahoo close x shares"
    storage.write_json(config.DATA / "fundamentals" / f"{symbol}.json", rec)
    return True
