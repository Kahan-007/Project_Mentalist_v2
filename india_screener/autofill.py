"""Automatic refresh of the inputs that used to be typed in by hand:

  - 10-year G-Sec yield   <- CNBC's public quote feed (symbol IN10Y), saved as scoring.risk_free
  - Gross NPA % (lenders) <- the quarterly results table on each company's Screener.in page
  - promoter % and FII + DII % <- Screener.in's quarterly shareholding table
  - promoter pledge %     <- NSE's pledge disclosures (% of promoter shares encumbered)
  - year-end prices + market cap for workbook companies <- Yahoo (workbook.fill_prices)

All three are free, need no login, and run from the daily job (G-Sec daily; the slower two weekly),
or on demand with `py -m india_screener autofill`. Values the user typed in are never overwritten:
fundamentals.set_manual records who supplied each field.
"""
import datetime as dt
import html
import json
import re
import time
from urllib.parse import quote

from . import config, fundamentals, net, prices, storage

STATE_PATH = config.DATA / "autofill_state.json"
GSEC_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            "?symbols=IN10Y&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
LENDERS = ("bank", "nbfc")


# ---------------------------------------------------------------- G-Sec yield

def fetch_gsec():
    """Current Indian 10-year government bond yield as a fraction (0.0712 for 7.12%)."""
    q = json.loads(net.fetch(GSEC_URL))["FormattedQuoteResult"]["FormattedQuote"][0]
    v = float(str(q["last"]).rstrip("%")) / 100
    if not 0.02 <= v <= 0.15:
        raise ValueError(f"implausible 10Y yield {v:.4f}; not saved")
    return v, q.get("last_time", "")


def update_gsec():
    v, when = fetch_gsec()
    user = storage.read_json(config.CONFIG_PATH) or {}
    user.setdefault("scoring", {})["risk_free"] = round(v, 5)
    storage.write_json(config.CONFIG_PATH, user)
    return {"risk_free": round(v, 5), "as_of": when}


# ---------------------------------------------------------------- Gross NPA %

def _cells(row_html, tag):
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
            for c in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", row_html, re.S)]


def _pct_num(s):
    return fundamentals._num((s or "").strip().rstrip("%").replace(",", "")) if s else None


def _table(page, start_pattern):
    """First <table> after `start_pattern`: (column headers, {row label: [cell text, ...]}).
    Row labels lose Screener's trailing ' +' expander marker."""
    m = re.search(start_pattern, page)
    if not m:
        return [], {}
    t = re.search(r"<table.*?</table>", page[m.start():], re.S)
    if not t:
        return [], {}
    tb = t.group(0)
    thead = re.search(r"<thead.*?</thead>", tb, re.S)
    heads = _cells(thead.group(0), "th")[1:] if thead else []
    rows = {}
    for tr in re.findall(r"<tr[^>]*>.*?</tr>", tb, re.S):
        cells = _cells(tr, "td")
        if cells:
            rows.setdefault(cells[0].rstrip("+ ").strip(), cells[1:])
    return heads, rows


def parse_gnpa(page):
    """(latest Gross NPA %, quarter label) from a Screener company page, or (None, None).
    Reads the 'Quarterly Results' section only, so the figure is the latest quarter's, not a year-end one."""
    heads, rows = _table(page, r'<section id="quarters"')
    vals = rows.get("Gross NPA %") or []
    for i in range(len(vals) - 1, -1, -1):
        v = _pct_num(vals[i])
        if v is not None:
            return v, (heads[i] if i < len(heads) else None)
    return None, None


def fetch_gnpa(symbol):
    """Banks report NPAs on the standalone entity; try that first, then consolidated."""
    for path in (f"{symbol}/", f"{symbol}/consolidated/"):
        try:
            page = net.fetch(f"https://www.screener.in/company/{path}").decode("utf-8", "replace")
        except net.FetchError:
            continue
        v, q = parse_gnpa(page)
        if v is not None:
            return v, q
    return None, None


def fill_gnpa(symbols=None, pause=1.0):
    """Fill Gross NPA % for every lender; skip values the user entered themselves."""
    done, missing, kept = [], [], []
    for sym in symbols or fundamentals.all_symbols():
        rec = fundamentals.load(sym)
        if not rec or rec.get("sector_type") not in LENDERS:
            continue
        if rec.get("manual", {}).get("gnpa_pct") is not None and "gnpa_pct" not in rec.get("manual_source", {}):
            kept.append(sym)
            continue
        v, q = fetch_gnpa(sym)
        if v is None:
            missing.append(sym)
        else:
            fundamentals.set_manual(sym, source=f"screener.in {q or 'latest quarter'}", gnpa_pct=v)
            done.append({"symbol": sym, "gnpa_pct": v, "quarter": q})
        time.sleep(pause)
    return {"filled": done, "not_found": missing, "kept_user_value": kept}


# ---------------------------------------------------------------- ownership (promoter / pledge / FII + DII)

def parse_shareholding(page):
    """{'promoter_pct', 'institutional_pct', 'quarter'} from Screener's quarterly shareholding table.
    Only figures actually printed are returned: a company with no 'Promoters' row gets no promoter_pct,
    and FII + DII is given only when both appear in the same (latest) quarter."""
    heads, rows = _table(page, r'id="quarterly-shp"')
    if not heads:
        return {}
    col = len(heads) - 1
    out = {"quarter": heads[col]}
    pick = lambda label: _pct_num(_at(rows.get(label), col))
    p = pick("Promoters")
    if p is not None:
        out["promoter_pct"] = p
    fii, dii = pick("FIIs"), pick("DIIs")
    if fii is not None and dii is not None:
        out["institutional_pct"] = round(fii + dii, 2)
    return out


def _at(lst, i):
    return lst[i] if lst and 0 <= i < len(lst) else None


def fetch_pledge(symbol):
    """NSE's pledge disclosure (from the quarterly shareholding pattern): encumbered promoter shares as a
    % of the promoter holding, plus the promoter holding itself. Returns {} if NSE has no filing."""
    body = net.fetch(f"https://www.nseindia.com/api/corporate-pledgedata?index=equities&symbol={quote(symbol)}",
                     retries=1, headers={"Referer": "https://www.nseindia.com/", "Accept": "application/json"})
    data = [d for d in json.loads(body).get("data") or [] if d.get("shp")]
    if not data:
        return {}
    latest = max(data, key=lambda d: dt.datetime.strptime(d["shp"], "%d-%b-%Y"))
    out = {"quarter": latest["shp"]}
    for key, field in (("pledge_pct", "percPromoterShares"), ("promoter_pct", "percPromoterHolding")):
        v = _pct_num(latest.get(field))
        if v is not None:
            out[key] = v
    return out


def fill_ownership(symbols=None, pause=1.0):
    """Promoter %, pledge % and FII + DII % for every imported company. Fields the user typed in are kept;
    fields no source reports are left blank, never guessed."""
    filled, partial, failed, kept = [], [], [], []
    fields = ("promoter_pct", "pledge_pct", "institutional_pct")
    for sym in symbols or fundamentals.all_symbols():
        rec = fundamentals.load(sym)
        if not rec:
            continue
        user_set = {k for k in fields if rec.get("manual", {}).get(k) is not None
                    and k not in rec.get("manual_source", {})}
        if len(user_set) == len(fields):
            kept.append(sym)
            continue
        got, errs = {}, []
        try:
            page = net.fetch(f"https://www.screener.in/company/{quote(sym)}/").decode("utf-8", "replace")
            sh = parse_shareholding(page)
            for k in ("promoter_pct", "institutional_pct"):
                if k in sh:
                    got[k] = (sh[k], f"screener.in {sh['quarter']}")
        except Exception as exc:
            errs.append(f"screener: {str(exc)[:80]}")
        try:
            pl = fetch_pledge(sym)
            if "pledge_pct" in pl:
                got["pledge_pct"] = (pl["pledge_pct"], f"NSE pledge filing {pl['quarter']}")
            if "promoter_pct" not in got and "promoter_pct" in pl:   # e.g. no promoter row on Screener
                got["promoter_pct"] = (pl["promoter_pct"], f"NSE pledge filing {pl['quarter']}")
        except Exception as exc:
            errs.append(f"nse: {str(exc)[:80]}")
        for k, (v, src) in got.items():
            if k not in user_set:
                fundamentals.set_manual(sym, source=src, **{k: v})
        missing = [k for k in fields if k not in got and k not in user_set]
        entry = {"symbol": sym, **{k: v for k, (v, _) in got.items()}}
        if not got:
            failed.append({"symbol": sym, "why": "; ".join(errs) or "no figures published"})
        elif missing:
            partial.append({**entry, "missing": missing, **({"errors": errs} if errs else {})})
        else:
            filled.append(entry)
        time.sleep(pause)
    return {"filled": len(filled), "partial": partial, "failed": failed, "kept_user_values": kept}


# ---------------------------------------------------------------- year-end prices

def fill_all_prices(progress=None):
    from . import workbook
    todo = [s for s in fundamentals.all_symbols() if fundamentals.needs_year_end_prices(fundamentals.load(s))]
    ok, failed = 0, []
    for i, sym in enumerate(todo, 1):
        try:
            if workbook.fill_prices(sym, prices.history(sym, rng="10y")):
                ok += 1
        except Exception as exc:
            failed.append(f"{sym}: {str(exc)[:120]}")
        if progress:
            progress(i, len(todo))
    return {"filled": ok, "total": len(todo), "failed": failed}


# ---------------------------------------------------------------- results-season financials

# Annual results (for March year-ends) are filed by end-May; Q2 results by mid-November. The extra month
# after each catches late filers and Screener's own update lag.
RESULTS_MONTHS = {5, 6, 11, 12}


def refresh_financials():
    from . import screener_fetch
    r = screener_fetch.refresh()
    report_financials_refresh(r)
    return {"updated": len(r["updated"]), "unchanged": r["unchanged"], "kept": len(r["kept"]), "failed": len(r["failed"])}


def report_financials_refresh(r):
    from . import appnotify
    if r["updated"]:
        appnotify.program(f"New results loaded for {len(r['updated'])} companies",
                          "\n".join(r["updated"][:60]) + ("\n..." if len(r["updated"]) > 60 else "") +
                          "\n\nScores now use these results. Year-end prices for any new year are filled on the next weekly run.",
                          level="info", key="fin-refresh-updated")
    if r["failed"]:
        appnotify.program(f"Financials refresh: {len(r['failed'])} companies could not be read",
                          "\n".join(f"{k}: {v}" for k, v in list(r["failed"].items())[:40]) +
                          "\n\nTheir previous figures are kept. It is retried next week.", key="fin-refresh-failed")


# ---------------------------------------------------------------- scheduling

def run(force=False, weekly_days=7):
    """G-Sec every call; NPA, ownership and year-end prices once every `weekly_days` unless force; the Screener
    financials refresh weekly during results season only. Errors are reported, not raised."""
    state = storage.read_json(STATE_PATH) or {}
    today = config.now_ist().date()
    out = {}
    jobs = [("gsec", update_gsec, 0), ("financials", refresh_financials, weekly_days), ("gnpa", fill_gnpa, weekly_days),
            ("ownership", fill_ownership, weekly_days), ("prices", fill_all_prices, weekly_days)]
    for name, fn, every in jobs:
        last = state.get(name)
        if name == "financials" and today.month not in RESULTS_MONTHS:
            out[name] = "skipped (outside results season: May-June, November-December)"
            continue
        if not force and last and (today - dt.date.fromisoformat(last)).days < every:
            out[name] = f"skipped (last run {last})"
            continue
        try:
            out[name] = fn()
            state[name] = today.isoformat()
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    storage.write_json(STATE_PATH, state)
    return out
