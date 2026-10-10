"""Cross-checks between data sources, so errors surface instead of flowing silently into scores.

Inside one Screener export:  balance sheet balances, cash flows add up, quarters add up to the year,
                             price x shares = market cap, share count matches share capital, freshness.
Screener vs Yahoo prices:    year-end prices agree (catches split/bonus adjustment errors on either side).
Nifty list vs NSE list:      symbols and ISINs agree (catches renamed or delisted symbols).
Each check returns {"check", "status": ok|warn|fail|skip, "detail"}.
"""
import datetime as dt

from . import config


def _r(check, status, detail):
    return {"check": check, "status": status, "detail": detail}


def _rel(a, b):
    return abs(a - b) / max(abs(a), abs(b), 1e-9)


def check_export(rec, today=None):
    today = today or config.now_ist().date()
    s, years, out = rec["series"], rec["years"], []
    get = lambda k: s.get(k) or [None] * len(years)

    tl, ta = get("total"), get("total_assets")
    pairs = [(y, a, b) for y, a, b in zip(years, tl, ta) if a is not None and b is not None]
    if pairs:
        worst = max(pairs, key=lambda p: _rel(p[1], p[2]))
        ok = _rel(worst[1], worst[2]) <= 0.005
        out.append(_r("Balance sheet balances", "ok" if ok else "fail",
                      f"liabilities = assets in all {len(pairs)} years" if ok else
                      f"{worst[0]}: liabilities {worst[1]:,.0f} vs assets {worst[2]:,.0f} cr"))
    else:
        out.append(_r("Balance sheet balances", "skip", "assets total not in file"))

    cfo, cfi, cff, net = get("cfo"), get("cfi"), get("cff"), get("net_cash_flow")
    bad = [y for y, a, b, c, d in zip(years, cfo, cfi, cff, net)
           if None not in (a, b, c, d) and abs(a + b + c - d) > max(1.0, 0.01 * max(abs(a), abs(b), abs(c)))]
    out.append(_r("Cash flows add up", "ok" if not bad else "warn",
                  "operating + investing + financing = net in every year" if not bad else f"mismatch in {', '.join(bad)}"))

    pbt, tax, np_ = get("pbt"), get("tax"), get("net_profit")
    assoc, mino = get("associates"), get("minority")
    # Where the source separates associates' profit and minority share, add them back so only true gaps remain.
    expect = [None if None in (p, t) else p - t + (a or 0) + (mi or 0) for p, t, a, mi in zip(pbt, tax, assoc, mino)]
    gaps = [(y, (n - e) / abs(e)) for y, e, n in zip(years, expect, np_) if None not in (e, n) and abs(e) > 1]
    big = [(y, g) for y, g in gaps if abs(g) > 0.10]
    out.append(_r("Net profit vs PBT - tax", "ok" if not big else "warn",
                  "within 10% every year" if not big else
                  "differs in " + ", ".join(f"{y[:4]} ({g:+.0%})" for y, g in big[-3:]) +
                  "; usually minority interest, associates or exceptional items. Screener's net profit is used."))

    q = rec.get("quarters") or {}
    qd = q.get("dates", [])
    fy_end = years[-1]
    idx = [i for i, d in enumerate(qd) if d <= fy_end and (dt.date.fromisoformat(fy_end) - dt.date.fromisoformat(d)).days < 360]
    if len(idx) == 4:
        msgs, status = [], "ok"
        for key, tol, label in (("sales", 0.02, "sales"), ("net_profit", 0.05, "net profit")):
            qs = [(q.get(key) or [None] * len(qd))[i] for i in idx]
            annual = get(key)[-1]
            if None in qs or annual is None:
                continue
            diff = _rel(sum(qs), annual)
            if diff > tol:
                status = "warn"
                msgs.append(f"{label}: quarters sum {sum(qs):,.0f} vs annual {annual:,.0f} cr ({diff:.1%})")
        out.append(_r(f"Quarters add up to FY{fy_end[:4]}", status, "; ".join(msgs) or "sales and profit agree"))
    else:
        out.append(_r("Quarters add up to the year", "skip", "the four quarters of the last year aren't all in the file"))

    meta, price, sh = rec.get("meta", {}), get("price"), get("shares_cr")
    if meta.get("market_cap") and meta.get("price") and sh[-1]:
        implied = meta["price"] * sh[-1]
        d = _rel(implied, meta["market_cap"])
        out.append(_r("Market cap = price x shares", "ok" if d <= 0.02 else "warn",
                      f"{meta['market_cap']:,.0f} cr reported, {implied:,.0f} implied ({d:.1%})"))
    cnt, fv, cap = get("shares_count"), get("face_value_row"), get("share_capital")
    if cnt[-1] and fv[-1] and cap[-1]:
        d = _rel(cnt[-1] * fv[-1] / 1e7, cap[-1])
        out.append(_r("Share count matches share capital", "ok" if d <= 0.02 else "warn",
                      f"{cnt[-1] / 1e7:,.2f} cr shares x Rs{fv[-1]:g} vs capital {cap[-1]:,.2f} cr ({d:.1%})"))
    if cnt[-1] and sh[-1]:
        d = _rel(cnt[-1] / 1e7, sh[-1])
        out.append(_r("Adjusted shares are current", "ok" if d <= 0.01 else "warn",
                      "latest adjusted share count equals shares outstanding" if d <= 0.01 else
                      f"adjusted {sh[-1]:,.2f} cr vs outstanding {cnt[-1] / 1e7:,.2f} cr; a split or bonus may not be reflected"))

    age = (today - dt.date.fromisoformat(fy_end)).days
    out.append(_r("Annual data is recent", "ok" if age <= 15 * 30 else "warn",
                  f"last annual report {fy_end}" + ("" if age <= 15 * 30 else "; export a fresh file")))
    if qd:
        qage = (today - dt.date.fromisoformat(qd[-1])).days
        out.append(_r("Quarterly data is recent", "ok" if qage <= 200 else "warn", f"last quarter {qd[-1]}"))
    out.append(_r("Years of history", "ok" if len(years) >= 8 else "warn",
                  f"{len(years)} annual reports" + ("" if len(years) >= 8 else "; 10 are recommended")))
    return out


def check_prices(rec, bars):
    """Screener's year-end prices vs Yahoo's split-adjusted closes on the same dates."""
    if not bars:
        return [_r("Screener vs Yahoo prices", "skip", "no Yahoo history")]
    closes = {b["date"]: b["close"] for b in bars}
    dates = sorted(closes)
    rows = []
    for y, p in zip(rec["years"], rec["series"].get("price") or []):
        if p is None or y < dates[0]:
            continue
        prior = [d for d in dates if d <= y]
        if not prior or (dt.date.fromisoformat(y) - dt.date.fromisoformat(prior[-1])).days > 7:
            continue
        rows.append((y, p, closes[prior[-1]]))
    if not rows:
        return [_r("Screener vs Yahoo prices", "skip", "no overlapping year-end dates")]
    out = []
    bad = [(y, a, b) for y, a, b in rows if _rel(a, b) > 0.03]
    if not bad:
        out.append(_r("Screener vs Yahoo prices", "ok", f"year-end prices agree within 3% for {len(rows)} years"))
    else:
        details = []
        for y, a, b in bad[:4]:
            ratio = a / b
            hint = next((f" (ratio {ratio:.2f}, looks like a split/bonus adjusted differently)"
                         for f in (2, 3, 4, 5, 10, 0.5, 1 / 3, 0.25, 0.2, 0.1) if abs(ratio - f) / f < 0.04), "")
            details.append(f"{y[:4]}: Screener {a:,.2f} vs Yahoo {b:,.2f}{hint}")
        out.append(_r("Screener vs Yahoo prices", "warn", "; ".join(details)))
    return out


def check_universe(index_list, nse_list):
    nse = {r["symbol"]: r for r in nse_list}
    by_isin = {r["isin"]: r["symbol"] for r in nse_list if r.get("isin")}
    out = []
    for r in index_list:
        n = nse.get(r["symbol"])
        if n is None:
            moved = by_isin.get(r["isin"])
            out.append(_r(r["symbol"], "warn", f"not in NSE list; ISIN now trades as {moved}" if moved else "not in NSE EQ list"))
        elif n.get("isin") and n["isin"] != r["isin"]:
            out.append(_r(r["symbol"], "warn", f"ISIN differs: index {r['isin']} vs NSE {n['isin']}"))
    return out or [_r("Index list vs NSE list", "ok", f"all {len(index_list)} symbols and ISINs agree")]
