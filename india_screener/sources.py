"""Plain-English provenance for every figure the dashboard shows ("NSE close 25-Sep-2026",
"pledge from NSE filing 30-Jun-2026", "financials from the Top 250 workbook"), used for hover tooltips."""
import datetime as dt

MANUAL_LABELS = {"promoter_pct": "Promoter holding", "pledge_pct": "Promoter pledge",
                 "institutional_pct": "FII + DII holding", "gnpa_pct": "Gross NPA"}


def nice_date(iso):
    try:
        return dt.date.fromisoformat(str(iso)[:10]).strftime("%d-%b-%Y")
    except (TypeError, ValueError):
        return str(iso or "")


def financials(rec):
    src = rec.get("source") or ""
    if src.startswith("Top 250 workbook"):
        return f"the Top 250 workbook ({src}); import time does not establish the source snapshot date"
    if src.startswith("Screener.in public page"):
        when = src.rsplit("retrieved ", 1)[-1].rstrip(")") if "retrieved " in src else rec.get("imported_at")
        return f"Screener.in public company page, fetched {nice_date(when)}"
    return f"Screener export file '{src}', imported {nice_date(rec.get('imported_at'))}"


def price(rec):
    meta = rec.get("meta") or {}
    ps, day = (meta.get("price_source") or "").lower(), meta.get("price_date") or meta.get("market_cap_date")
    if "nse" in ps:
        what = "NSE official closing price"
    elif "yahoo" in ps:
        what = "Yahoo Finance closing price"
    elif "snapshot" in ps and "user" in ps:
        what = "price snapshot file you uploaded"
    elif "snapshot" in ps:
        what = "market-cap snapshot in the Top 250 workbook"
    else:
        what = "price in the imported Screener export"
    return f"{what}, {nice_date(day)}" if day else what


def for_record(rec, score=None):
    fin, px = financials(rec), price(rec)
    last_year = nice_date(rec["years"][-1]) if rec.get("years") else "latest year"
    out = {
        "financials": f"Financial statements: {fin}",
        "price": f"Price: {px}",
        "market_cap": f"Market cap = {px} x shares outstanding (shares from {fin})",
        "pe": f"P/E = market cap ({px}) / net profit for {((score or {}).get('pe_basis') or 'FY ' + last_year)} ({fin})",
        "pb": f"P/B = market cap ({px}) / book value (equity) at {last_year} ({fin})",
        "roce": f"ROCE = EBIT / average (equity + borrowings), year to {last_year}. Calculated from {fin}",
        "roic": f"Operating ROIC = (PBT + interest − other income) x (1 − observed tax rate) / average (equity + borrowings - cash), year to {last_year}. Calculated from {fin}",
        "roe": f"ROE = net profit / average equity, year to {last_year}. Calculated from {fin}",
        "scores": f"Calculated by the program from {fin}, the {px}, and the ownership / NPA figures",
        "manual": {},
    }
    src = rec.get("manual_source") or {}
    for k, label in MANUAL_LABELS.items():
        v = (rec.get("manual") or {}).get(k)
        if v is not None:
            out["manual"][k] = f"{label}: {src[k]}" if src.get(k) else f"{label}: entered by you"
    return out
