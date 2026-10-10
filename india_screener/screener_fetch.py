"""Fetch a company's annual financials from its public Screener.in page, for companies the combined
workbook doesn't cover (e.g. Nifty LargeMidcap 250 members outside the top 250 by market cap).

The workbook itself was built from Screener's tables, so this reads the same tables (plus Screener's
public row breakdowns for Profit for EPS, receivables/inventory/cash and capex) into the workbook's
line-item layout and runs them through workbook.build_record. Records from both sources are therefore
scored identically. Nothing is estimated: a row Screener doesn't publish stays empty.
"""
import html
import json
import re
from urllib.parse import quote

from . import config, fundamentals, index250, net, storage, workbook

BASE = "https://www.screener.in"

# Screener row label (lower case, '+' and spaces trimmed) -> workbook item name
ROWS = {
    "profit-loss": {"sales": "Sales / Revenue", "revenue": "Sales / Revenue", "other income": "Other Income",
                    "depreciation": "Depreciation", "interest": "Interest / Finance Cost",
                    "profit before tax": "Profit Before Tax", "net profit": "Net Profit", "eps in rs": "EPS in Rs",
                    "dividend payout %": "Dividend Payout %"},
    "balance-sheet": {"equity capital": "Equity Capital", "reserves": "Reserves", "borrowings": "Borrowings",
                      "borrowing": "Borrowings", "other liabilities": "Other Liabilities",
                      "total liabilities": "Total equity and liabilities", "fixed assets": "Fixed Assets (net, including intangibles)",
                      "cwip": "CWIP", "investments": "Investments", "other assets": "Other Assets", "total assets": "Total Assets"},
    "cash-flow": {"cash from operating activity": "Cash from Operations",
                  "cash from investing activity": "Cash from Investing Activity",
                  "cash from financing activity": "Cash from Financing Activity", "net cash flow": "Net Cash Flow"},
}
SCHEDULES = {   # (parent row, section) -> {breakdown row: workbook item}
    ("Net Profit", "profit-loss"): {"Profit for EPS": "Profit for EPS", "Minority share": "Minority share",
                                    "Profit from Associates": "Profit from Associates"},
    ("Other Assets", "balance-sheet"): {"Trade receivables": "Trade Receivables", "Inventories": "Inventory",
                                       "Cash Equivalents": "Cash & Equivalents"},
    ("Cash from Investing Activity", "cash-flow"): {"Fixed assets purchased": "Capital Expenditure"},
}
PERIOD_RE = re.compile(r"^[A-Z][a-z]{2} \d{4}(?: \d+m)?$")


def _text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").split())


def _section(page, sid):
    m = re.search(rf'<section id="{sid}".*?</section>', page, re.S)
    return m.group(0) if m else ""


def _table(sec):
    """({period label: column index}, {row label: [cells]}) of a section's first table."""
    t = re.search(r"<table.*?</table>", sec, re.S)
    if not t:
        return {}, {}
    heads = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", t.group(0), re.S)]
    cols = {h: i for i, h in enumerate(heads) if PERIOD_RE.match(h)}
    rows = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t.group(0), re.S):
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if cells:
            rows.setdefault(cells[0].rstrip("+ ").strip().lower(), cells)
    return cols, rows


def _num(s):
    s = (s or "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_page(page, symbol):
    """Workbook-style (company row, items, periods) from one Screener company page."""
    if not re.search(rf"NSE:\s*{re.escape(symbol)}\b", _text(page)):
        raise ValueError(f"page does not show NSE: {symbol}")
    pl_cols, pl = _table(_section(page, "profit-loss"))
    years = [p for p in pl_cols if p][-10:]          # the workbook keeps at most 10 years
    if len(years) < 3:
        raise ValueError(f"only {len(years)} annual periods on Screener")
    items = {}
    for sid, mapping in ROWS.items():
        cols, rows = (pl_cols, pl) if sid == "profit-loss" else _table(_section(page, sid))
        for label, item in mapping.items():
            if label in rows and item not in items:
                items[item] = [_num(rows[label][cols[y]]) if y in cols and cols[y] < len(rows[label]) else None
                               for y in years]
    if "tax %" in pl:   # Screener shows tax as % of |profit before tax| (negative for a tax credit)
        pbt = items.get("Profit Before Tax") or [None] * len(years)
        pct = [_num(pl["tax %"][pl_cols[y]]) for y in years]
        items["Tax"] = [abs(p) * t / 100 if p is not None and t is not None else None for p, t in zip(pbt, pct)]
    if "Dividend Payout %" in items:   # the workbook stores payout as a fraction
        items["Dividend Payout %"] = [x / 100 if x is not None else None for x in items["Dividend Payout %"]]
    name = _text((re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) or [None, ""])[1])
    mcap = re.search(r"Market Cap\s*</span>.*?<span class=\"number\">([\d,.]+)</span>", page, re.S)
    # Quarterly results (last 12 three-month quarters), so P/E can use the trailing four quarters once they
    # run past the last annual report. Irregular-length periods (e.g. '4m') are skipped.
    q_cols, qr = _table(_section(page, "quarters"))
    qlabels = [p for p in q_cols if not p.endswith("m")][-12:]
    quarters = {"dates": [workbook.period(p)[0] for p in qlabels]}
    for key, labels in (("sales", ("sales", "revenue")), ("net_profit", ("net profit",))):
        row = next((qr[l] for l in labels if l in qr), None)
        quarters[key] = [_num(row[q_cols[p]]) if row and q_cols[p] < len(row) else None for p in qlabels]
    return {"name": name, "market_cap": _num(mcap.group(1)) if mcap else None, "quarters": quarters}, items, years


def _schedules(company_id, consolidated, years, items):
    for (parent, section), mapping in SCHEDULES.items():
        url = (f"{BASE}/api/company/{company_id}/schedules/?parent={quote(parent)}&section={section}"
               + ("&consolidated=" if consolidated else ""))
        try:
            data = json.loads(net.fetch(url))
        except Exception:
            continue          # a missing breakdown just leaves those rows empty
        for row, item in mapping.items():
            vals = data.get(row)
            if isinstance(vals, dict):
                items[item] = [_num(vals.get(y)) for y in years]
    if "Capital Expenditure" in items:   # Screener shows purchases as an outflow; the workbook as a positive amount
        items["Capital Expenditure"] = [-x if x is not None else None for x in items["Capital Expenditure"]]


def fetch(symbol, basis=None):
    """Build and return a fundamentals record (not saved). Prefers consolidated figures, except for
    banks and NBFCs, where (as in the workbook) the standalone lender's accounts are used.
    basis='consolidated'/'standalone' fetches only that version (used when refreshing a known company)."""
    symbol = symbol.upper()
    lender = index250.by_symbol().get(symbol, {}).get("suggested_type") in ("bank", "nbfc")
    got, errors = {}, []
    for consolidated in ((basis == "consolidated",) if basis else (True, False)):
        url = f"{BASE}/company/{quote(symbol)}/" + ("consolidated/" if consolidated else "")
        try:
            page = net.fetch(url, timeout=20).decode("utf-8", "replace")
            rec = _build(symbol, page, consolidated, url)
            got[consolidated] = rec
        except Exception as exc:
            errors.append(f"{'consolidated' if consolidated else 'standalone'}: {exc}")
    if not got:
        raise ValueError("; ".join(errors))
    # Preferred basis (standalone for lenders, like the workbook) unless the other is clearly better:
    # a later latest year (e.g. consolidated accounts discontinued), or much longer history.
    pref, other = got.get(not lender), got.get(lender)
    if pref and other and (other["years"][-1] > pref["years"][-1]
                           or (len(pref["years"]) < 6 and len(other["years"]) > len(pref["years"]))):
        pref = other
    return pref or other


def _build(symbol, page, consolidated, url):
    info, items, years = parse_page(page, symbol)
    cid = re.search(r'data-company-id="(\d+)"', page)
    if cid:
        _schedules(cid.group(1), consolidated, years, items)
    # workbook item names -> the record keys build_record expects (same mapping the workbook uses)
    it = {workbook.ITEMS[k]: v for k, v in items.items() if k in workbook.ITEMS}
    it["_np_owners"] = it.get("profit_for_eps")         # Screener's Profit for EPS = profit attributable to owners
    ix = index250.by_symbol().get(symbol, {})
    row = {"NSE Symbol": symbol, "Company": info["name"] or ix.get("name"), "Market Cap (INR crore)": info["market_cap"],
           "Basis": "Consolidated" if consolidated else "Standalone", "Industry": ix.get("industry"),
           "ISIN": ix.get("isin"), "Market-cap Rank": None}
    per = [workbook.period(y) for y in years]
    today = config.now_ist().date().isoformat()
    rec, why = workbook.build_record(row, it, per, today)
    if not rec:
        raise ValueError(why)
    rec["source"] = f"Screener.in public page {url} (retrieved {today})"
    rec["quarters"] = info["quarters"]
    return rec


def merge_refresh(new, old):
    """Combine a freshly fetched record with the stored one. Returns (record to save or None, what changed).
    Never goes backwards (an older latest year), keeps your manual entries and business-type choice, keeps
    year-end prices already filled for matching years, and keeps a fresher daily closing price."""
    if new["years"][-1] < old["years"][-1]:
        return None, "Screener's latest year is older than the stored one; kept stored figures"
    is_export = bool((old.get("quarters") or {}).get("dates")) and not str(old.get("source", "")).startswith("Screener.in public page")
    newer_year = new["years"][-1] > old["years"][-1]
    oq, nq = (old.get("quarters") or {}).get("dates") or [], (new.get("quarters") or {}).get("dates") or []
    newer_quarter = bool(nq) and (not oq or nq[-1] > oq[-1])
    if is_export and not newer_year:
        return None, "kept your Screener export (Screener has no newer annual year)"
    for k in ("manual", "manual_source", "mcap_rank", "screener_industry"):
        if old.get(k) is not None:
            new[k] = old[k]
    if old.get("sector_type_set_by_user"):
        new["sector_type"], new["sector_type_set_by_user"] = old["sector_type"], True
    prices_by_year = dict(zip(old["years"], (old.get("series") or {}).get("price") or []))
    new["series"]["price"] = [prices_by_year.get(y) for y in new["years"]]
    om, shares = old.get("meta") or {}, (new["meta"].get("shares") or 0) / 1e7
    if om.get("price_date") and om.get("price") and shares:
        new["meta"].update(price=om["price"], market_cap=om["price"] * shares, market_cap_date=om["price_date"],
                           price_date=om["price_date"], price_source=om.get("price_source"))
    new["imported_at"] = config.now_ist().isoformat()
    what = ("new annual results " + new["years"][-1][:4] if newer_year else "") + \
           (("; " if newer_year else "") + "new quarter " + nq[-1][:7] if newer_quarter else "")
    return new, what or "no new periods; figures re-checked"


def refresh(symbols=None, pause=1.5, progress=None):
    """Results-season refresh: re-read every stored company from Screener and save what's newer."""
    import time
    syms = symbols or fundamentals.all_symbols()
    updated, unchanged, kept, failed = [], [], [], {}
    for i, sym in enumerate(syms, 1):
        old = fundamentals.load(sym)
        if not old:
            continue
        try:
            try:
                new = fetch(sym, basis=old.get("basis"))
            except ValueError:     # e.g. the stored basis has no annual figures on Screener: try the other accounts
                new = fetch(sym)
            rec, what = merge_refresh(new, old)
            if rec is None:
                kept.append(f"{sym}: {what}")
            else:
                storage.write_json(config.DATA / "fundamentals" / f"{sym}.json", rec)
                (updated if what.startswith("new") else unchanged).append(f"{sym}: {what}" if what.startswith("new") else sym)
        except Exception as exc:
            failed[sym] = f"{type(exc).__name__}: {str(exc)[:150]}"
        if progress:
            progress(i, len(syms))
        time.sleep(pause)
    return {"updated": updated, "unchanged": len(unchanged), "kept": kept, "failed": failed, "checked": len(syms)}


def save(rec):
    """Store a fetched record, keeping anything the user or autofill already set."""
    old = fundamentals.load(rec["symbol"]) or {}
    for k in ("manual", "manual_source"):
        rec[k] = old.get(k, {})
    if old.get("sector_type_set_by_user"):
        rec["sector_type"], rec["sector_type_set_by_user"] = old["sector_type"], True
    rec["imported_at"] = config.now_ist().isoformat()
    storage.write_json(config.DATA / "fundamentals" / f"{rec['symbol']}.json", rec)
    _add_to_size_universe(rec)


def _add_to_size_universe(rec):
    """Give the company a market-cap rank among the workbook's top-250 list so its SEBI size bucket
    (large/mid/small) is right. Existing ranks are left as the workbook set them."""
    path = config.DATA / "universe_mcap250.json"
    uni = storage.read_json(path)
    mc = rec["meta"].get("market_cap")
    if not uni or not mc:
        return
    comps = [c for c in uni["companies"] if c["symbol"] != rec["symbol"]]
    rank = 1 + sum(1 for c in comps if (c.get("market_cap_cr") or 0) > mc and not c.get("rank_estimated"))
    comps.append({"symbol": rec["symbol"], "name": rec["name"], "rank": rank, "market_cap_cr": mc,
                  "rank_estimated": True, "basis": rec["basis"]})
    uni["companies"] = comps
    storage.write_json(path, uni)


def missing_index_members():
    have = set(fundamentals.all_symbols())
    return [r["symbol"] for r in index250.load() if r["symbol"] not in have]


def fill_missing(symbols=None, pause=1.5):
    import time
    done, failed = [], {}
    for sym in symbols or missing_index_members():
        try:
            rec = fetch(sym)
            save(rec)
            done.append({"symbol": sym, "years": f"{rec['years'][0][:4]}-{rec['years'][-1][:4]}", "basis": rec["basis"]})
        except Exception as exc:
            failed[sym] = f"{type(exc).__name__}: {str(exc)[:150]}"
        time.sleep(pause)
    return {"imported": done, "failed": failed}
