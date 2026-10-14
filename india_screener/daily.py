"""Daily job: refresh prices + news for shortlist and holdings, advance events, alert, write a report."""
import json
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import appnotify, autofill, config, news, price_snapshot, recommendations, notify, outbox, portfolio, prices, shortlist, tracker, universe


def watchlist():
    items = {}
    for s in shortlist.load().get("stocks", []):
        items[s["symbol"]] = {"symbol": s["symbol"], "exchange": s.get("exchange", "NSE"), "name": s.get("name"),
                              "aliases": s.get("aliases", []), "scores": s.get("scores"), "shortlisted": True, "held": False}
    for sym, pos in portfolio.holdings().items():
        it = items.setdefault(sym, {"symbol": sym, "exchange": pos["exchange"], "name": None, "aliases": [],
                                    "scores": None, "shortlisted": False})
        it["held"] = True
    if any(not it["name"] for it in items.values()):
        try:
            names = {r["symbol"]: r["name"] for r in universe.load()}
        except Exception:
            names = {}
        for it in items.values():
            it["name"] = it["name"] or names.get(it["symbol"], it["symbol"])
    return list(items.values())


def _pct(x):
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def format_alert(a, item):
    d = a["detail"].get("dip", {})
    lines = []
    heads = tracker.headlines_for(a["symbol"], a["session"])
    if a["type"] == "dip":
        title = f"{a['symbol']}: news-linked abnormal drop on {a['session']}"
        lines.append(f"Abnormal return {_pct(d.get('abnormal_return'))} vs threshold -{d.get('threshold', 0) * 100:.2f}% "
                     f"(z = {d.get('z_score', 0):.1f}); raw {_pct(d.get('raw_return'))}, Nifty {_pct(d.get('market_return'))}.")
        if d.get("volume_ratio") is not None:
            lines.append(f"Volume {d['volume_ratio']:.1f}x normal.")
    else:
        lv = a["detail"].get("levelling", {})
        title = f"{a['symbol']}: price has levelled out after the {a['session']} drop"
        lines.append(f"Now {_pct(lv.get('recovery_vs_pre_event'))} vs pre-event close; trough was "
                     f"{_pct(lv.get('trough_vs_pre_event'))} on {lv.get('trough_date')}. Decide manually whether to act.")
    if item and item.get("held"):
        lines.append("You hold this stock.")
    for h in heads[:4]:
        cats = ", ".join(json.loads(h["categories"])) or "uncategorised"
        lines.append(f"- {h['title']} ({h['source']}; {cats})\n{h['url']}")
    lines.append("A same-session drop is evidence of the market reacting, not proof that this headline caused it.")
    return title, "\n".join(lines)


def run(cfg=None, send_alerts=True):
    cfg = cfg or config.load_config()
    config.ensure_dirs()
    today = config.now_ist().date().isoformat()
    errors, alerts = {}, []
    auto = autofill.run()   # G-Sec yield daily; NPA % and year-end prices weekly
    for name, res in auto.items():
        if isinstance(res, dict) and res.get("error"):
            errors[f"autofill:{name}"] = res["error"]
    bench = prices.benchmark(cfg)
    trading_dates = [b["date"] for b in bench]
    cur = price_snapshot.refresh(cfg=cfg, benchmark_date=bench[-1]["date"])   # one NSE file, Yahoo fallback
    if cur["nse_error"]:
        errors["current prices (NSE)"] = cur["nse_error"] + f"; Yahoo used for {len(cur['yahoo_updated'])} stocks"
    if cur["yahoo_failed"]:
        errors["current prices (Yahoo fallback)"] = f"{len(cur['yahoo_failed'])} stocks still stale: " + \
            ", ".join(sorted(cur["yahoo_failed"])[:15])
    items = watchlist()
    bars_by_symbol = {}

    def work(it):
        bars = prices.history(it["symbol"], it["exchange"], cfg=cfg)
        heads = news.fetch_news(it["symbol"], it["name"], it["aliases"], days=cfg["news_lookback_days"])
        return bars, heads

    with ThreadPoolExecutor(max_workers=max(1, int(cfg["workers"]))) as pool:
        futures = {it["symbol"]: (it, pool.submit(work, it)) for it in items}
        for sym, (it, fut) in futures.items():
            try:
                bars, heads = fut.result()
                bars_by_symbol[sym] = bars
                tracker.store_news(sym, heads)
                tracker.group_into_events(sym, it["exchange"], trading_dates)
                for a in tracker.evaluate(sym, bars, bench, cfg, publish=send_alerts):
                    alerts.append((a, it))
            except Exception as exc:
                errors[sym] = f"{type(exc).__name__}: {exc}"
                with open(config.LOGS / "errors.log", "a", encoding="utf-8") as f:
                    f.write(f"[{today}] {sym}\n{traceback.format_exc()}\n")

    formatted = [format_alert(a, it) for a, it in alerts]
    send_problems = set()
    if send_alerts:
        send_problems.update(outbox.flush(cfg))
    for (a, it), (title, body) in zip(alerts, formatted):
        appnotify.stock(title, body, symbol=a["symbol"])
        recommendations.safe_record(a["symbol"], "alert_" + a["type"], note=f"{title}\n{body}",
                                    headlines=tracker.headlines_for(a["symbol"], a["session"]))
    if send_problems:
        errors["pop-up / Telegram delivery"] = "; ".join(sorted(send_problems))
    analyses = {it["symbol"]: recommendations.analyse(it["symbol"]) for it in items}
    recommendations.log_signal_changes(items, analyses, publish=send_alerts, cfg=cfg)
    if send_alerts:
        send_problems.update(outbox.flush(cfg))
        if send_problems:
            errors['pop-up / Telegram delivery'] = '; '.join(sorted(send_problems))
    path = write_report(today, bench, items, bars_by_symbol, formatted, errors, analyses)
    for name, msg in errors.items():
        appnotify.program(*explain_error(name, msg), key=f"daily:{name}")
    return {"report": str(path), "alerts": len(formatted), "errors": errors, "tracked": len(items)}


def explain_error(name, msg):
    """(title, plain-English body) for a daily-check problem, saying what it affects and what happens next."""
    if name.startswith("current prices (NSE)"):
        return ("NSE closing prices unavailable today",
                f"{msg}\n\nValuations were refreshed from Yahoo Finance instead. Nothing to do unless this repeats for several days.")
    if name.startswith("current prices (Yahoo"):
        return ("Some stocks still have an old price",
                f"{msg}\n\nTheir P/E, P/B and margin of safety use the last price that did load. The next daily check tries again.")
    if name.startswith("autofill:"):
        job = {"gsec": "10-year G-Sec yield", "gnpa": "Gross NPA figures", "ownership": "promoter / pledge / FII+DII figures",
               "prices": "year-end price history", "financials": "results-season financials refresh"}.get(name.split(":", 1)[1], name)
        return (f"Automatic update failed: {job}", f"{msg}\n\nThe previous values are kept and it will be retried on the next run.")
    if name.startswith("pop-up"):
        return ("Alert delivery problem", f"{msg}\n\nThe alerts are still listed here in the notification centre.")
    return (f"Daily check problem: {name}",
            f"{msg}\n\nPrices and news for this stock may be missing from today's check; it is retried tomorrow.")


def analysis_section(items, analyses):
    """Same Buffett/Munger/Lynch analysis for holdings as for shortlisted stocks."""
    out = ["", "## Financial analysis (holdings and shortlist)", "",
           "| Stock | Why tracked | Buffett | Munger | Lynch | Matches | Lynch type | P/E | Price as of | Needs attention |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for it in sorted(items, key=lambda i: (not i["held"], i["symbol"])):
        why = " + ".join(w for w, on in (("held", it["held"]), ("shortlisted", it["shortlisted"])) if on)
        r, err = analyses.get(it["symbol"], (None, "not analysed"))
        if not r:
            out.append(f"| {it['symbol']} | {why} | - | - | - | - | - | - | - | {err} |")
            continue
        sc = lambda k: "n/a" if r[k]["score"] is None else f"{r[k]['score']:.0f}%"
        issues = []
        for k in ("buffett", "munger", "lynch"):
            issues += [f"{k}: fails {e}" for e in r[k]["failed_essentials"]]
            issues += [f"{k}: needs {e}" for e in r[k]["unknown_essentials"]]
        date = next((w.split("as of ")[1] for w in r["warnings"] if w.startswith("market cap as of")), "-")
        pe = "n/a" if r["pe"] is None else f"{r['pe']:.1f}"
        out.append(f"| {it['symbol']} | {why} | {sc('buffett')} | {sc('munger')} | {sc('lynch')} | "
                   f"{', '.join(r['matches']) or 'none'} | {r['lynch_category']} | {pe} | {date} | {'; '.join(issues) or '-'} |")
    return out


def write_report(today, bench, items, bars_by_symbol, formatted, errors, analyses=None):
    out = [f"# Daily report {today}", "", f"Benchmark last completed session: {bench[-1]['date']}",
           f"Tracked: {len(items)} stocks ({sum(i['shortlisted'] for i in items)} shortlisted, {sum(i['held'] for i in items)} held)", ""]
    out += ["## New alerts", ""]
    out += [f"### {t}\n{b}\n" for t, b in formatted] or ["None today.", ""]
    open_ev = tracker.list_events(["provisional", "dip_confirmed"])
    out += ["## Open events", "", "| Stock | Session | Status | Abnormal return | Since event vs pre-event |", "|---|---|---|---|---|"]
    for e in open_ev:
        d = json.loads(e["detail"])
        out.append(f"| {e['symbol']} | {e['event_session']} | {e['status']} | {_pct(d.get('dip', {}).get('abnormal_return'))} | "
                   f"{_pct(d.get('levelling', {}).get('recovery_vs_pre_event'))} |")
    if not open_ev:
        out.append("| - | - | none | - | - |")
    out += ["", "## Portfolio (valued at last completed close)", "",
            "| Stock | Qty | Avg cost | Last close | Value | Unrealised | Realised | Dividends |", "|---|---|---|---|---|---|---|---|"]
    for sym, p in sorted(portfolio.positions().items()):
        bars = bars_by_symbol.get(sym)
        last = bars[-1]["close"] if bars else None
        value = float(p["quantity"]) * last if last is not None else None
        unreal = value - float(p["invested"]) if value is not None else None
        fmt = lambda x: "n/a" if x is None else f"{x:,.2f}"
        out.append(f"| {sym} | {p['quantity']} | {fmt(float(p['avg_cost']))} | {fmt(last)} | {fmt(value)} | {fmt(unreal)} | "
                   f"{fmt(float(p['realised']))} | {fmt(float(p['dividends']))} |")
    if analyses is not None:
        out += analysis_section(items, analyses)
    if errors:
        out += ["", "## Errors", ""] + [f"- {s}: {m}" for s, m in errors.items()]
    path = config.REPORTS / f"{today}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path
