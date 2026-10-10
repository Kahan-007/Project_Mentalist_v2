"""Local dashboard: http://127.0.0.1:8765  (listens on this computer only)."""
import datetime as dt
import json
import re
import threading
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (appnotify, autofill, config, daily, fundamentals, index250, news, notify, portfolio, presets, prices,
               recommendations, scoring, sources, shortlist, storage,
               tracker, universe, verify)

WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 15_000_000
SYMBOL_RE = universe.SYMBOL_RE
JOB = {"running": False, "started": None, "finished": None, "result": None, "error": None}
NEWS_CACHE = {}
LOCK = threading.Lock()


def size_ranks():
    """SEBI-style rank by market cap. Preferred source: the top-250-by-market-cap list from the workbook
    (anything not in it ranks above 250, i.e. small cap). Otherwise ranks imported Nifty-250 companies
    once (nearly) all are imported."""
    from . import workbook
    mu = workbook.mcap_universe()
    if len(mu) >= 240:
        ranks = {c["symbol"]: c["rank"] for c in mu if c.get("rank")}
        return _RankMap(ranks)
    idx = index250.by_symbol()
    caps = []
    for sym in fundamentals.all_symbols():
        if sym in idx:
            rec = fundamentals.load(sym)
            mc = (rec.get("meta") or {}).get("market_cap")
            if mc:
                caps.append((mc, sym))
    if len(caps) < 240:
        return {}
    caps.sort(reverse=True)
    return {sym: i + 1 for i, (_, sym) in enumerate(caps)}


class _RankMap(dict):
    """Symbols missing from a complete top-250 list rank beyond 250 (SEBI small cap)."""
    def get(self, k, default=None):
        return super().get(k, 251)


def _score(rec, st=None, ranks=None):
    ranks = size_ranks() if ranks is None else ranks
    return scoring.score_company(rec, st, ranks.get(rec["symbol"]))


def _dec(x):
    return float(x) if x is not None else None


def _last_closes(symbols_exchanges):
    out = {}

    def one(se):
        sym, ex = se
        try:
            b = prices.history(sym, ex)
            return sym, {"close": b[-1]["close"], "date": b[-1]["date"],
                         "prev": b[-2]["close"] if len(b) > 1 else None}
        except Exception as exc:
            return sym, {"error": str(exc)[:160]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for sym, v in pool.map(one, symbols_exchanges):
            out[sym] = v
    return out


def api_portfolio(q, body):
    pos = portfolio.positions()
    closes = _last_closes([(s, p["exchange"]) for s, p in pos.items() if p["quantity"] > 0])
    rows, tot_inv, tot_val = [], 0.0, 0.0
    for s, p in sorted(pos.items()):
        c = closes.get(s, {})
        qty, inv = _dec(p["quantity"]), _dec(p["invested"])
        value = qty * c["close"] if c.get("close") is not None and qty else None
        if value is not None:
            tot_inv += inv
            tot_val += value
        rows.append({"symbol": s, "exchange": p["exchange"], "quantity": qty, "avg_cost": _dec(p["avg_cost"]),
                     "invested": inv, "last_close": c.get("close"), "close_date": c.get("date"),
                     "day_change": (c["close"] / c["prev"] - 1) if c.get("close") and c.get("prev") else None,
                     "value": value, "unrealised": value - inv if value is not None else None,
                     "realised": _dec(p["realised"]), "dividends": _dec(p["dividends"]),
                     "price_error": c.get("error")})
    txns = [{**t, "quantity": float(t["quantity"]), "price": float(t["price"]), "fees": float(t["fees"]),
             "amount": float(t["amount"])} for t in portfolio.transactions()]
    return {"positions": rows, "transactions": txns[::-1],
            "totals": {"invested_priced": tot_inv, "value": tot_val, "unrealised": tot_val - tot_inv}}


def api_txn(q, body):
    b = json.loads(body)
    g = lambda k, d: b.get(k) or d   # a blank string from an unused/hidden form field should mean "not given"
    tid = portfolio.add(b["kind"], b["symbol"], b["date"], quantity=g("quantity", 0), price=g("price", 0),
                        fees=g("fees", 0), amount=g("amount", 0), ratio=g("ratio", None),
                        exchange=g("exchange", "NSE"), note=g("note", ""))
    if b["kind"].upper() in ("BUY", "SELL"):
        recommendations.safe_record(b["symbol"], "bought" if b["kind"].upper() == "BUY" else "sold",
                                    note=f"{b['kind'].upper()} {g('quantity', 0)} @ {g('price', 0)} on {b['date']}")
    return {"id": tid}


def api_txn_delete(q, body):
    return {"deleted": portfolio.delete(json.loads(body)["id"])}


def api_import_zerodha(q, body):
    r = portfolio.import_zerodha_tradebook(body.decode("utf-8-sig"))
    for sym in r["symbols"]:
        recommendations.safe_record(sym, "trades_imported",
                                    note="new trades imported from a Zerodha tradebook; this analysis is as of the "
                                         "import date, not the trade dates")
    return r


def api_universe(q, body):
    term = (q.get("q", [""])[0] or "").strip().upper()
    if len(term) < 1:
        return []
    try:
        recs = universe.load()
    except Exception as exc:
        return [{"symbol": term, "name": f"(NSE list unavailable: {str(exc)[:60]})"}] if SYMBOL_RE.match(term) else []
    starts = [r for r in recs if r["symbol"].startswith(term)]
    contains = [r for r in recs if r not in starts and (term in r["symbol"] or term in r["name"].upper())]
    return [{"symbol": r["symbol"], "name": r["name"]} for r in (starts + contains)[:15]]


def _symbol(q):
    s = (q.get("symbol", [""])[0] or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise ValueError("choose a valid NSE/BSE symbol")
    return s


def _news_cached(sym, name, aliases):
    key = sym
    hit = NEWS_CACHE.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    items = news.fetch_news(sym, name, aliases, days=14)
    NEWS_CACHE[key] = (time.time(), items)
    return items


def api_stock(q, body):
    sym = _symbol(q)
    exchange = q.get("exchange", ["NSE"])[0]
    try:
        u = universe.lookup(sym)
    except Exception:
        u = None
    sl = {s["symbol"]: s for s in shortlist.load().get("stocks", [])}
    name = (u or {}).get("name") or sl.get(sym, {}).get("name") or sym
    out = {"symbol": sym, "name": name, "in_nse_list": u is not None, "shortlisted": sym in sl,
           "aliases": sl.get(sym, {}).get("aliases", [])}
    try:
        bars = prices.history(sym, exchange)
        closes = [b["close"] for b in bars]
        out["price"] = {"last": closes[-1], "date": bars[-1]["date"], "series": [[b["date"], b["close"]] for b in bars[-250:]],
                        "high_52w": max(closes[-250:]), "low_52w": min(closes[-250:]),
                        "change_1y": closes[-1] / closes[-250] - 1 if len(closes) >= 250 else None}
    except Exception as exc:
        out["price_error"] = str(exc)[:200]
    rec = fundamentals.load(sym)
    if rec:
        out["fundamentals"] = {"imported_at": rec["imported_at"], "basis": rec["basis"], "sector_type": rec["sector_type"],
                               "source": rec["source"], "years": rec["years"], "file_name": rec.get("name"),
                               "manual": rec.get("manual", {})}
        try:
            out["score"] = _score(rec)
            out["score"]["headroom"] = scoring.price_headroom(rec, None, size_ranks().get(sym))
        except Exception as exc:
            out["score_error"] = f"{type(exc).__name__}: {exc}"
        out["sources"] = sources.for_record(rec, out.get("score"))
        out["checks"] = verify.check_export(rec)
    pos = portfolio.positions().get(sym)
    if pos and pos["quantity"] > 0:
        out["position"] = {"quantity": _dec(pos["quantity"]), "avg_cost": _dec(pos["avg_cost"]), "invested": _dec(pos["invested"])}
    try:
        out["news"] = _news_cached(sym, name, out["aliases"])
    except Exception as exc:
        out["news_error"] = str(exc)[:200]
    out["events"] = [{**e, "detail": json.loads(e["detail"])} for e in tracker.list_events() if e["symbol"] == sym][:10]
    out["screener_url"] = f"https://www.screener.in/company/{sym}/consolidated/"
    return out


def _name_matches(file_name, symbol):
    if not file_name:
        return True
    try:
        u = universe.lookup(symbol)
    except Exception:
        return True
    if not u:
        return True
    a = set(re.findall(r"[a-z]{3,}", universe.short_name(u["name"]).lower()))
    b = set(re.findall(r"[a-z]{3,}", universe.short_name(file_name).lower()))
    return bool(a & b) or not a or not b


def api_import_financials(q, body):
    sym = _symbol(q)
    basis = q.get("basis", ["consolidated"])[0]
    sector = q.get("sector", ["general"])[0]
    fname = q.get("filename", ["upload"])[0][:120]
    parsed = fundamentals.parse_file(body, fname)
    if not _name_matches(parsed.get("name"), sym) and q.get("force", ["0"])[0] != "1":
        raise ValueError(f"this file is for '{parsed.get('name')}', not {sym}. Check you uploaded the right company "
                         "(tick 'import anyway' if the names just differ).")
    ix = index250.by_symbol().get(sym, {})
    rec = fundamentals.save(sym, parsed, basis, sector, fname, industry=ix.get("industry"))
    return {"ok": True, "years": rec["years"], "score": _score(rec), "checks": verify.check_export(rec)}


def api_import_auto(q, body):
    """Bulk import: work out which company the file is from its own 'COMPANY NAME' cell."""
    fname = q.get("filename", ["upload"])[0][:120]
    basis = q.get("basis", ["consolidated"])[0]
    parsed = fundamentals.parse_file(body, fname)
    forced = (q.get("symbol", [""])[0] or "").strip().upper()
    idx = index250.by_symbol()
    if forced:
        if not SYMBOL_RE.match(forced):
            raise ValueError("invalid symbol")
        sym, conf, alt = forced, 1.0, None
    else:
        sym, conf, alt = index250.match(parsed.get("name") or "", fname)
        if conf < 0.6:
            try:
                nse = [{"symbol": r["symbol"], "name": r["name"]} for r in universe.load()]
                s2, c2, a2 = index250.match(parsed.get("name") or "", fname, universe=nse)
                if c2 > conf:
                    sym, conf, alt = s2, c2, a2
            except Exception:
                pass
    if not sym or conf < 0.6:
        return {"ok": False, "file": fname, "company": parsed.get("name"), "best_guess": sym, "alternative": alt,
                "reason": "could not tell which company this is; pick the symbol and retry"}
    existing = fundamentals.load(sym)
    ix = idx.get(sym, {})
    sector = (existing or {}).get("sector_type") or ix.get("suggested_type", "general")
    rec = fundamentals.save(sym, parsed, basis, sector, fname, industry=ix.get("industry"))
    checks = verify.check_export(rec)
    return {"ok": True, "file": fname, "company": parsed.get("name"), "symbol": sym, "confidence": conf,
            "in_top250": sym in idx, "sector_type": sector, "years": len(rec["years"]),
            "last_year": rec["years"][-1], "problems": [c for c in checks if c["status"] in ("warn", "fail")]}


BUCKETS = {
    "all_three": "All three",
    "quality_core": "Quality core (Buffett + Munger)",
    "two_of_three": "Two of three (with Lynch)",
    "lynch_only": "Growth (Lynch only)",
    "buffett_only": "Buffett only",
    "munger_only": "Munger only",
    "none": "No match",
}


def bucket_of(matches):
    m = set(matches)
    if len(m) == 3:
        return "all_three"
    if m == {"buffett", "munger"}:
        return "quality_core"
    if len(m) == 2:
        return "two_of_three"
    if m == {"lynch"}:
        return "lynch_only"
    if m == {"buffett"}:
        return "buffett_only"
    if m == {"munger"}:
        return "munger_only"
    return "none"


def base_universe(which="nifty"):
    from . import workbook
    nifty = index250.load()
    mcap = workbook.mcap_universe()
    nsyms = {r["symbol"] for r in nifty}
    mranks = {c["symbol"]: c.get("rank") for c in mcap}
    if which == "mcap" and mcap:
        base = [{"symbol": c["symbol"], "name": c["name"], "industry": c["industry"], "suggested_type": c["suggested_type"]} for c in mcap]
    elif which == "both" and mcap:
        base = nifty + [{"symbol": c["symbol"], "name": c["name"], "industry": c["industry"], "suggested_type": c["suggested_type"]}
                        for c in mcap if c["symbol"] not in nsyms]
    else:
        base = nifty
    for r in base:
        r["in_nifty"] = r["symbol"] in nsyms
        r["mcap_rank"] = mranks.get(r["symbol"])
    return base


def universe_rows(which="nifty"):
    st = scoring.settings()
    ranks = size_ranks()
    sl = {s["symbol"] for s in shortlist.load().get("stocks", [])}
    held = set(portfolio.holdings())
    rows = []
    for r in base_universe(which):
        rec = fundamentals.load(r["symbol"])
        row = {"symbol": r["symbol"], "name": r["name"], "industry": r["industry"], "suggested_type": r["suggested_type"],
               "in_nifty": r["in_nifty"], "mcap_rank": r["mcap_rank"],
               "imported": bool(rec), "shortlisted": r["symbol"] in sl, "held": r["symbol"] in held}
        if rec:
            row.update(years=len(rec["years"]), last_year=rec["years"][-1], sector_type=rec["sector_type"],
                       imported_at=rec.get("imported_at", "")[:10],
                       source="workbook" if not (rec.get("quarters") or {}).get("dates") else "screener export",
                       has_prices=any(v is not None for v in (rec["series"].get("price") or [])),
                       manual=rec.get("manual", {}))
            try:
                sc = _score(rec, st, ranks)
                row.update(category=sc["lynch_category"], provisional=sc["provisional"], size=sc["size"],
                           market_cap_cr=sc["market_cap_cr"], pe=sc["pe"], pb=sc["pb"], returns=sc["returns"],
                           src=sources.for_record(rec, sc),
                           all_three=sc["all_three"], match_count=len(sc["matches"]), matches=sc["matches"],
                           bucket=bucket_of(sc["matches"]), value_multiple=sc["valuation"]["multiple"],
                           **{k: {"score": sc[k]["score"], "matches": sc[k]["matches"], "coverage": sc[k]["coverage"],
                                  "failed": sc[k]["failed_essentials"], "unknown": sc[k]["unknown_essentials"]}
                              for k in ("buffett", "munger", "lynch")})
                row["signal"], row["signal_note"] = recommendations.assess(row["held"], row["shortlisted"], sc, st)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
        if "signal" not in row:
            row["signal"], row["signal_note"] = recommendations.assess(row["held"], row["shortlisted"], None, st)
        rows.append(row)
    return rows, st, ranks


def api_top250(q, body):
    from . import workbook
    which = q.get("universe", ["nifty"])[0]
    rows, st, ranks = universe_rows(which)
    done = [r for r in rows if r["imported"]]
    return {"rows": rows, "imported": len(done), "total": len(rows),
            "ten_year": sum(1 for r in done if r.get("years", 0) >= 10),
            "size_basis": ("market-cap rank from the workbook snapshot" if isinstance(ranks, _RankMap)
                           else "rank among the 250" if ranks else "rupee cut-offs (import all 250 to rank)"),
            "pass_score": st["pass_score"], "buckets": BUCKETS, "universe": which,
            "mcap_universe_available": bool(workbook.mcap_universe()),
            "missing_prices": sum(1 for r in done if not r.get("has_prices"))}


def export_csv(q):
    """CSV of the rows currently shown on the Top 250 tab, in the order shown."""
    import csv as _csv
    import io as _io
    wanted = [x for x in (q.get("symbols", [""])[0] or "").split(",") if x]
    rows, st, _ = universe_rows("both")
    by = {r["symbol"]: r for r in rows}
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["Symbol", "Company", "Industry", "Business type", "Lynch category", "Size", "Market cap (Rs cr)", "P/E", "P/B",
                "ROCE % (latest)", "ROIC % (latest)", "ROCE % (5y median)", "ROIC % (5y median)", "Buffett score", "Buffett match", "Munger score", "Munger match", "Lynch score", "Lynch match",
                "Bucket", "Must-pass tests failed", "Years of data", "Last annual report", "Provisional", "Shortlisted"])
    fmt = lambda x, d=1: "" if x is None else f"{x:.{d}f}"
    for sym in wanted or [r["symbol"] for r in rows]:
        r = by.get(sym)
        if not r:
            continue
        g = lambda k: r.get(k) or {}
        failed = "; ".join(f"{k.title()}: {', '.join(g(k).get('failed', []))}" for k in ("buffett", "munger", "lynch") if g(k).get("failed"))
        w.writerow([r["symbol"], r["name"], r["industry"], r.get("sector_type") or r["suggested_type"], r.get("category", ""),
                    r.get("size", ""), fmt(r.get("market_cap_cr"), 0), fmt(r.get("pe")), fmt(r.get("pb")),
                    *[fmt(g("returns").get(k) * 100) if g("returns").get("applicable") and g("returns").get(k) is not None else ""
                      for k in ("roce", "roic", "roce_5y", "roic_5y")],
                    fmt(g("buffett").get("score")), "yes" if g("buffett").get("matches") else "",
                    fmt(g("munger").get("score")), "yes" if g("munger").get("matches") else "",
                    fmt(g("lynch").get("score")), "yes" if g("lynch").get("matches") else "",
                    BUCKETS.get(r.get("bucket"), "") if r["imported"] else "not imported", failed,
                    r.get("years", ""), r.get("last_year", ""), "yes" if r.get("provisional") else "",
                    "yes" if r["shortlisted"] else ""])
    return buf.getvalue().encode("utf-8-sig")


def api_shortlist_add_many(q, body):
    b = json.loads(body)
    syms = [x.upper() for x in b.get("symbols", []) if SYMBOL_RE.match(x.upper())]
    added, skipped = [], []
    current = {s["symbol"] for s in shortlist.load().get("stocks", [])}
    for sym in syms:
        if sym in current:
            continue
        if not fundamentals.load(sym):
            skipped.append(sym)
            continue
        api_shortlist_add(q, json.dumps({"symbol": sym}).encode())
        added.append(sym)
    return {"added": added, "skipped_not_imported": skipped}


def api_import_workbook(q, body):
    from . import workbook
    keep = q.get("keep_exports", ["1"])[0] != "0"
    return workbook.import_workbook(body, keep_exports=keep)


def api_latest_prices(q, body):
    from . import workbook
    return workbook.apply_latest_prices(body.decode("utf-8-sig"))


PRICE_JOB = {"running": False, "done": 0, "total": 0, "errors": [], "finished": None}


def _fill_prices_job(symbols):
    from . import workbook
    for sym in symbols:
        try:
            workbook.fill_prices(sym, prices.history(sym, rng="10y"))
        except Exception as exc:
            PRICE_JOB["errors"].append(f"{sym}: {str(exc)[:120]}")
        PRICE_JOB["done"] += 1
    PRICE_JOB["running"] = False
    PRICE_JOB["finished"] = config.now_ist().isoformat()
    if PRICE_JOB["errors"]:
        appnotify.program(f"Year-end prices: {len(PRICE_JOB['errors'])} of {PRICE_JOB['total']} companies failed",
                          "\n".join(PRICE_JOB["errors"][:30]) + "\n\nTheir retained-earnings test stays blank until a later fill works.",
                          key="fill-prices")


def api_fill_prices(q, body):
    """Year-end prices (for the retained-earnings test) and a fresh market cap, from Yahoo, for records missing them."""
    with LOCK:
        if PRICE_JOB["running"]:
            return PRICE_JOB
        todo = [s for s in fundamentals.all_symbols() if fundamentals.needs_year_end_prices(fundamentals.load(s))]
        PRICE_JOB.update(running=True, done=0, total=len(todo), errors=[], finished=None)
    threading.Thread(target=_fill_prices_job, args=(todo,), daemon=True).start()
    return PRICE_JOB


def api_price_job(q, body):
    return PRICE_JOB


FIN_JOB = {"running": False, "done": 0, "total": 0, "finished": None, "result": None}


def _refresh_financials_job(symbols):
    from . import screener_fetch
    try:
        r = screener_fetch.refresh(symbols, progress=lambda i, n: FIN_JOB.update(done=i, total=n))
        FIN_JOB["result"] = r
        autofill.report_financials_refresh(r)
    except Exception as exc:
        appnotify.program("Financials refresh stopped", f"{type(exc).__name__}: {exc}", level="error", key="fin-refresh")
    FIN_JOB.update(running=False, finished=config.now_ist().isoformat())


def api_refresh_financials(q, body):
    """Re-read every company's financials from Screener now (normally automatic in results season)."""
    with LOCK:
        if FIN_JOB["running"]:
            return FIN_JOB
        syms = fundamentals.all_symbols()
        FIN_JOB.update(running=True, done=0, total=len(syms), finished=None, result=None)
    threading.Thread(target=_refresh_financials_job, args=(syms,), daemon=True).start()
    return FIN_JOB


def api_fin_job(q, body):
    return FIN_JOB


def api_notifications(q, body):
    appnotify.check_schedule_health()
    kind = (q.get("kind", [""])[0] or None)
    return appnotify.items(kind if kind in ("stock", "program") else None)


def api_notifications_read(q, body):
    b = json.loads(body or b"{}")
    appnotify.mark_read(b.get("ids"))
    return {"unread": appnotify.unread_count()}


def api_unread(q, body):
    return {"unread": appnotify.unread_count()}


def api_index_upload(q, body):
    recs = index250.save_user_list(body.decode("utf-8-sig"))
    return {"ok": True, "companies": len(recs)}


def api_sector(q, body):
    b = json.loads(body)
    rec = fundamentals.load(b["symbol"].upper())
    if not rec:
        raise ValueError("import the financials first")
    if b["sector_type"] not in fundamentals.SECTOR_TYPES:
        raise ValueError("unknown business type")
    rec["sector_type"] = b["sector_type"]
    rec["sector_type_set_by_user"] = True
    storage.write_json(config.DATA / "fundamentals" / f"{rec['symbol']}.json", rec)
    return {"ok": True}


def api_verify_prices(q, body):
    sym = _symbol(q)
    rec = fundamentals.load(sym)
    if not rec:
        raise ValueError("import the financials first")
    bars = prices.history(sym, rng="10y")
    return {"checks": verify.check_prices(rec, bars)}


def api_manual(q, body):
    b = json.loads(body)
    rec = fundamentals.set_manual(b["symbol"].upper(), **{k: b.get(k) for k in ("promoter_pct", "pledge_pct", "institutional_pct", "gnpa_pct")})
    return {"ok": True, "manual": rec["manual"]}


def api_manual_bulk(q, body):
    """Save promoter/pledge/institutional/NPA for many companies in one request. Blank fields are left
    untouched (not cleared) so a partially-filled row from an earlier save is never wiped by a later one."""
    rows = json.loads(body).get("rows", [])
    saved, errors = [], []
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        fields = {k: r.get(k) for k in ("promoter_pct", "pledge_pct", "institutional_pct", "gnpa_pct") if r.get(k) not in (None, "")}
        if not fields:
            continue
        try:
            fundamentals.set_manual(sym, **fields)
            saved.append(sym)
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
    return {"saved": saved, "errors": errors}


def api_scores(q, body):
    st = scoring.settings()
    ranks = size_ranks()
    sl = {s["symbol"] for s in shortlist.load().get("stocks", [])}
    pos = portfolio.holdings()
    rows = []
    for sym in sorted(set(fundamentals.all_symbols()) | set(pos) | sl):
        rec = fundamentals.load(sym)
        base = {"symbol": sym, "shortlisted": sym in sl, "held": sym in pos,
                "quantity": float(pos[sym]["quantity"]) if sym in pos else None}
        if not rec:
            s, why = recommendations.assess(sym in pos, sym in sl, None, st)
            rows.append({**base, "error": "financials not imported", "signal": s, "signal_note": why})
            continue
        try:
            r = _score(rec, st, ranks)
            rows.append({**base, "name": r["name"], "category": r["lynch_category"], "size": r["size"],
                         "sector_type": r["sector_type"], "basis": r["basis"], "pe": r["pe"],
                         "industry": rec.get("industry") or rec.get("screener_industry") or "Other",
                         "returns": r["returns"], "src": sources.for_record(rec, r),
                         "price": (rec.get("meta") or {}).get("price"), "price_date": (rec.get("meta") or {}).get("price_date"),
                         **{k: {"score": r[k]["score"], "coverage": r[k]["coverage"], "matches": r[k]["matches"],
                                "failed": r[k]["failed_essentials"]}
                            for k in ("buffett", "munger", "lynch")},
                         "matches": r["matches"], "match_count": len(r["matches"]), "all_three": r["all_three"],
                         "imported_at": rec["imported_at"],
                         **dict(zip(("signal", "signal_note"), recommendations.assess(sym in pos, sym in sl, r, st))),
                         "value_multiple": r["valuation"]["multiple"],
                         # price trigger only for your own stocks (it re-scores ~20 times, so not for all 250)
                         "headroom": scoring.price_headroom(rec, st, ranks.get(sym)) if (sym in pos or sym in sl) else None})
        except Exception as exc:
            rows.append({**base, "error": f"{type(exc).__name__}: {exc}"})
    rows.sort(key=lambda r: (-(r.get("match_count") or 0),
                             -sum((r.get(k) or {}).get("score") or 0 for k in ("buffett", "munger", "lynch"))))
    return {"rows": rows, "pass_score": st["pass_score"], "price_discipline": st.get("price_discipline") or 0}


def api_shortlist(q, body):
    return shortlist.load()


def api_shortlist_add(q, body):
    b = json.loads(body)
    sym = b["symbol"].upper()
    rec = fundamentals.load(sym)
    aliases = [a.strip() for a in b.get("aliases", []) if a.strip()]
    data = shortlist.load()
    already = any(s["symbol"] == sym for s in data["stocks"])
    stocks = [s for s in data["stocks"] if s["symbol"] != sym]
    entry = {"symbol": sym, "exchange": b.get("exchange", "NSE"), "aliases": aliases}
    if rec:
        r = _score(rec)
        entry["name"] = rec.get("name")
        entry["scores"] = {k: r[k]["score"] for k in ("buffett", "munger", "lynch")}
        entry["matches"] = r["matches"]
        entry["bucket"] = bucket_of(r["matches"])
    stocks.append(entry)
    shortlist.save(stocks, data.get("source", "dashboard"))
    if not already:
        recommendations.safe_record(sym, "shortlisted")
    return {"ok": True}


def api_shortlist_remove(q, body):
    sym = json.loads(body)["symbol"].upper()
    n = shortlist.remove(sym)
    if n:
        recommendations.safe_record(sym, "removed_from_shortlist")
    return {"removed": n}


def api_recommendations(q, body):
    sym = (q.get("symbol", [""])[0] or "").strip().upper() or None
    if q.get("id"):
        rows = [r for r in recommendations.history(sym, limit=100000, full=True) if str(r["id"]) == q["id"][0]]
        return rows[0] if rows else {}
    return {"rows": recommendations.history(sym)}


def api_shortlist_all_three(q, body):
    added = []
    for r in api_scores(q, body)["rows"]:
        if r.get("all_three") and not r.get("shortlisted"):
            api_shortlist_add(q, json.dumps({"symbol": r["symbol"]}).encode())
            added.append(r["symbol"])
    return {"added": added}


def api_events(q, body):
    evs = [{**e, "detail": json.loads(e["detail"])} for e in tracker.list_events()][:200]
    log = config.LOGS / "alerts.log"
    reports = sorted(config.REPORTS.glob("*.md"))[-1:] if config.REPORTS.exists() else []
    return {"events": evs, "alerts_log": log.read_text(encoding="utf-8")[-6000:] if log.exists() else "",
            "latest_report": reports[0].read_text(encoding="utf-8") if reports else "", "job": JOB}


def _run_job():
    try:
        JOB["result"] = daily.run()
    except Exception as exc:
        JOB["error"] = f"{type(exc).__name__}: {exc}"
        appnotify.program("The daily check stopped with an error",
                          f"{JOB['error']}\n\nNo alerts or report were produced by this run.\n{traceback.format_exc()[-1500:]}",
                          level="error", key="daily-crash")
    finally:
        JOB["running"] = False
        JOB["finished"] = config.now_ist().isoformat()


def api_run_daily(q, body):
    with LOCK:
        if JOB["running"]:
            return {"started": False, "job": JOB}
        JOB.update(running=True, started=config.now_ist().isoformat(), finished=None, result=None, error=None)
    threading.Thread(target=_run_job, daemon=True).start()
    return {"started": True, "job": JOB}


SETTING_KEYS = {"risk_free", "equity_premium", "terminal_growth", "pass_score", "min_coverage",
                "large_cap_min_cr", "mid_cap_min_cr", "price_discipline"}


# Alert sensitivity knobs editable on the Settings page: (section, key) -> (min, max, whole number?)
ALERT_KEYS = {("event", "min_abnormal_drop"): (0.01, 0.20, False), ("event", "sigma_multiple"): (1.0, 4.0, False),
              ("event", "window_sessions"): (1, 5, True), ("event", "expire_after_sessions"): (20, 250, True),
              ("levelling", "sessions"): (3, 20, True), ("levelling", "min_sessions_after_event"): (3, 30, True),
              ("levelling", "atr_ratio_max"): (1.0, 3.0, False), ("levelling", "drift_sigma_max"): (0.5, 3.0, False),
              ("levelling", "no_new_low_sessions"): (1, 10, True)}


def api_settings(q, body):
    cfg = config.load_config()
    return {"scoring": scoring.settings(), "telegram_configured": bool(cfg["notify"]["telegram_bot_token"]),
            "windows_toast": cfg["notify"]["windows_toast"],
            "ntfy_topic": cfg["notify"]["ntfy_topic"], "ntfy_server": cfg["notify"]["ntfy_server"],
            "discord_configured": bool(cfg["notify"]["discord_webhook"]),
            "alerts": {f"{s}.{k}": cfg[s][k] for s, k in ALERT_KEYS},
            "alert_defaults": {f"{s}.{k}": config.DEFAULTS[s][k] for s, k in ALERT_KEYS}}


def api_settings_save(q, body):
    b = json.loads(body)
    if b.get("scoring"):
        presets.ensure_baseline()
    user = storage.read_json(config.CONFIG_PATH) or {}
    sc = user.setdefault("scoring", {})
    for k, v in b.get("scoring", {}).items():
        if k in SETTING_KEYS:
            v = float(v)
            if k in ("risk_free", "equity_premium", "terminal_growth") and not (0 <= v < 0.5):
                raise ValueError(f"{k} must be a fraction, e.g. 0.068 for 6.8%")
            if k == "price_discipline" and not (v == 0 or 1.2 <= v <= 10):
                raise ValueError("price discipline must be 0 (off) or a multiple between 1.2 and 10")
            sc[k] = v
    for name, v in (b.get("alerts") or {}).items():
        sec, _, key = name.partition(".")
        if (sec, key) not in ALERT_KEYS:
            continue
        lo, hi, whole = ALERT_KEYS[(sec, key)]
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f"{key.replace('_', ' ')} must be between {lo} and {hi}")
        user.setdefault(sec, {})[key] = int(round(v)) if whole else v
    n = user.setdefault("notify", {})
    if "windows_toast" in b:
        n["windows_toast"] = bool(b["windows_toast"])
    for k in ("telegram_bot_token", "telegram_chat_id"):
        if b.get(k):
            n[k] = str(b[k]).strip()
    if "ntfy_topic" in b:          # blank clears it
        topic = str(b["ntfy_topic"] or "").strip()
        if topic and not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", topic):
            raise ValueError("ntfy topic: 6-64 letters, digits, - or _ (no spaces), exactly as in the ntfy app")
        n["ntfy_topic"] = topic
    if b.get("ntfy_server"):
        srv = str(b["ntfy_server"]).strip().rstrip("/")
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", srv):
            raise ValueError("ntfy server must look like https://ntfy.sh")
        n["ntfy_server"] = srv
    if b.get("discord_webhook"):
        hook = str(b["discord_webhook"]).strip()
        if not re.fullmatch(r"https://(discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/\d+/[\w-]+", hook):
            raise ValueError("That doesn't look like a Discord webhook URL (https://discord.com/api/webhooks/...)")
        n["discord_webhook"] = hook
    if b.get("discord_remove"):
        n["discord_webhook"] = ""
    storage.write_json(config.CONFIG_PATH, user)
    if b.get("scoring"):
        presets.record("saved")
    return api_settings(q, body)


def api_presets(q, body):
    return presets.state()


def api_presets_action(q, body):
    b = json.loads(body or b"{}")
    act = b.get("action")
    if act == "undo":
        return presets.undo()
    if act == "redo":
        return presets.redo()
    if act == "reset":
        return presets.reset()
    if act == "save":
        return presets.save_preset(b.get("name"))
    if act == "load":
        return presets.load_preset(b.get("name"))
    if act == "delete":
        return presets.delete_preset(b.get("name"))
    if act == "version":
        return presets.load_version(int(b.get("index")))
    raise ValueError("unknown preset action")


def api_notify_test(q, body):
    return {"problems": notify.send("Screener test alert", "If you can see this, alerts work.")}


GET = {"/api/portfolio": api_portfolio, "/api/universe": api_universe, "/api/stock": api_stock,
       "/api/scores": api_scores, "/api/shortlist": api_shortlist, "/api/events": api_events,
       "/api/recommendations": api_recommendations, "/api/presets": api_presets,
       "/api/notifications": api_notifications, "/api/unread": api_unread, "/api/fin-job": api_fin_job,
       "/api/settings": api_settings, "/api/top250": api_top250, "/api/verify-prices": api_verify_prices,
       "/api/price-job": api_price_job}
POST = {"/api/presets": api_presets_action, "/api/notifications/read": api_notifications_read,
        "/api/refresh-financials": api_refresh_financials, "/api/txn": api_txn, "/api/txn/delete": api_txn_delete, "/api/import/zerodha": api_import_zerodha,
        "/api/import/financials": api_import_financials, "/api/manual": api_manual,
        "/api/shortlist/add": api_shortlist_add, "/api/shortlist/remove": api_shortlist_remove,
        "/api/shortlist/all-three": api_shortlist_all_three, "/api/run-daily": api_run_daily,
        "/api/settings": api_settings_save, "/api/notify-test": api_notify_test,
        "/api/import/auto": api_import_auto, "/api/shortlist/add-many": api_shortlist_add_many,
        "/api/import/workbook": api_import_workbook, "/api/fill-prices": api_fill_prices,
        "/api/import/latest-prices": api_latest_prices,
        "/api/manual-bulk": api_manual_bulk, "/api/index-list": api_index_upload, "/api/sector": api_sector}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload, mime="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _local(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _dispatch(self, table, body=b""):
        u = urlparse(self.path)
        fn = table.get(u.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            self._send(200, fn(parse_qs(u.query), body))
        except (ValueError, KeyError, portfolio.LedgerError, fundamentals.ImportError_) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            with open(config.LOGS / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"[{dt.datetime.now()}] {self.path}\n{traceback.format_exc()}\n")
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self):
        if not self._local():
            return self._send(403, {"error": "local access only"})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/app.js":
            return self._send(200, (WEB / "app.js").read_bytes(), "text/javascript; charset=utf-8")
        if u.path == "/export.csv":
            if not self._local():
                return self._send(403, {"error": "local access only"})
            data = export_csv(parse_qs(u.query))
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="screener_{config.now_ist().date()}.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/template.csv":
            return self._send(200, fundamentals.template_csv().encode(), "text/csv; charset=utf-8")
        self._dispatch(GET)

    def do_POST(self):
        # Custom header + same-origin check: other websites can't trigger actions on this local server.
        length = int(self.headers.get("Content-Length") or 0)
        if not self._local() or self.headers.get("X-Screener") != "1" or \
                (self.headers.get("Origin") and self.headers.get("Origin") != f"http://{self.headers.get('Host')}"):
            if 0 < length <= MAX_BODY:
                self.rfile.read(length)   # drain it so the refusal arrives cleanly instead of a connection reset
            return self._send(403, {"error": "forbidden"})
        if not 0 <= length <= MAX_BODY:
            return self._send(413, {"error": "upload too large"})
        self._dispatch(POST, self.rfile.read(length))


def serve(port=8765, open_browser=True):
    config.ensure_dirs()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SystemExit(f"Port {port} is busy ({exc}). Is the screener already running? Try --port {port + 1}.")
    url = f"http://127.0.0.1:{port}/"
    print(f"Screener running at {url}  (close this window or press Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
