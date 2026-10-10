"""Command line: py -m india_screener <command> ...   (run 'py -m india_screener -h' for help)"""
import argparse
import json
import sys
from pathlib import Path

from . import config, daily, fundamentals, net, news, notify, portfolio, prices, schedule, shortlist, tracker, universe


def _name(symbol):
    try:
        return (universe.lookup(symbol) or {}).get("name", symbol)
    except Exception:
        return symbol   # NSE list unreachable: fall back to the ticker


def _p(x):
    return f"{x * 100:+.2f}%" if isinstance(x, (int, float)) else "n/a"


def cmd_check(a):
    ok = True
    cfg = config.load_config()
    config.ensure_dirs()
    for label, fn in [
        ("NSE universe", lambda: f"{len(universe.load(refresh=True))} EQ stocks"),
        ("Benchmark", lambda: (lambda b: f"{len(b)} sessions, last {b[-1]['date']} close {b[-1]['close']:.2f}")(prices.benchmark(cfg))),
        (f"Prices {a.symbol}", lambda: (lambda b: f"{len(b)} sessions, last {b[-1]['date']} close {b[-1]['close']:.2f}")(prices.history(a.symbol, force=True, cfg=cfg))),
        (f"News {a.symbol}", lambda: (lambda n: f"{len(n)} relevant headlines; latest: {n[0]['title'] if n else '-'}")(
            news.fetch_news(a.symbol, _name(a.symbol), days=7))),
    ]:
        try:
            print(f"[ OK ] {label}: {fn()}")
        except Exception as exc:
            ok = False
            print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
    return 0 if ok else 1


def cmd_universe(a):
    recs = universe.load(refresh=a.refresh)
    print(f"{len(recs)} NSE EQ stocks")
    for r in recs[: a.show]:
        print(f"{r['symbol']:<14} {r['name']}")


def cmd_prices(a):
    for b in prices.history(a.symbol, a.exchange, force=a.force)[-a.days:]:
        print(f"{b['date']}  O {b['open']:>10.2f}  H {b['high']:>10.2f}  L {b['low']:>10.2f}  C {b['close']:>10.2f}  adj {b['adjclose']:>10.2f}  V {b['volume']:>12,.0f}")


def cmd_news(a):
    name = a.name or _name(a.symbol)
    print("Search terms:", news.search_terms(name, a.symbol, a.alias or []))
    for n in news.fetch_news(a.symbol, name, a.alias or [], days=a.days):
        print(f"{n['published_utc'][:16]}  [{', '.join(n['categories']) or '-'}]  {n['title']}  ({n['source']})")


def cmd_shortlist(a):
    from . import recommendations
    current = {s["symbol"] for s in shortlist.load().get("stocks", [])}
    if a.action == "add":
        for s in a.symbols:
            shortlist.add(s.upper(), a.exchange, aliases=a.alias or [])
            if s.upper() not in current:
                recommendations.safe_record(s.upper(), "shortlisted")
    elif a.action == "remove":
        for s in a.symbols:
            if shortlist.remove(s.upper()):
                recommendations.safe_record(s.upper(), "removed_from_shortlist")
    data = shortlist.load()
    print(f"Shortlist ({data.get('source', '-')}, {data.get('generated_at', '-')}):")
    for s in data["stocks"]:
        print(" ", s["symbol"], s.get("exchange", "NSE"), s.get("aliases", ""), s.get("scores", ""))


def cmd_portfolio(a):
    if a.action == "add":
        if not a.kind or not a.symbol:
            raise ValueError("usage: portfolio add KIND SYMBOL --date YYYY-MM-DD ...")
        tid = portfolio.add(a.kind, a.symbol, a.date or config.now_ist().date().isoformat(), quantity=a.qty, price=a.price, fees=a.fees,
                            amount=a.amount, ratio=a.ratio, exchange=a.exchange, note=a.note)
        print(f"Added transaction #{tid}")
        if a.kind.upper() in ("BUY", "SELL"):
            from . import recommendations
            recommendations.safe_record(a.symbol.upper(), "bought" if a.kind.upper() == "BUY" else "sold",
                                        note=f"{a.kind.upper()} {a.qty} @ {a.price} on {a.date or 'today'}")
    elif a.action == "import-zerodha":
        if not a.file:
            raise ValueError("pass --file path\\to\\tradebook.csv")
        from . import server
        print(server.api_import_zerodha({}, Path(a.file).read_bytes()))
    elif a.action == "delete":
        print(f"Deleted {portfolio.delete(a.id)} transaction(s)")
    elif a.action == "txns":
        for t in portfolio.transactions():
            extra = f"{t['ratio_num']}:{t['ratio_den']}" if t["ratio_num"] else f"amt {t['amount']}" if t["kind"] == "DIVIDEND" else f"{t['quantity']} @ {t['price']} fees {t['fees']}"
            print(f"#{t['id']:<4} {t['trade_date']} {t['kind']:<8} {t['symbol']:<12} {extra}")
        return
    for sym, p in sorted(portfolio.positions().items()):
        state = "OPEN  " if p["quantity"] else "closed"
        print(f"{state} {sym:<12} qty {p['quantity']:>6}  avg cost {p['avg_cost']:>10.2f}  invested {p['invested']:>12.2f}  "
              f"realised {p['realised']:>10.2f}  dividends {p['dividends']:>8.2f}")


def cmd_events(a):
    rows = tracker.list_events(None if a.all else ["provisional", "dip_confirmed"])
    for e in rows:
        d = json.loads(e["detail"])
        print(f"{e['event_session']}  {e['symbol']:<12} {e['status']:<16} abnormal {_p(d.get('dip', {}).get('abnormal_return'))}"
              f"  vs pre-event now {_p(d.get('levelling', {}).get('recovery_vs_pre_event'))}")
    if not rows:
        print("No events.")


def cmd_import_folder(a):
    from . import server
    files = sorted(Path(a.folder).glob("*.xlsx")) + sorted(Path(a.folder).glob("*.csv"))
    if not files:
        raise ValueError(f"no .xlsx or .csv files in {a.folder}")
    ok = 0
    for f in files:
        try:
            r = server.api_import_auto({"filename": [f.name], "basis": [a.basis]}, f.read_bytes())
        except Exception as exc:
            print(f"[FAIL] {f.name}: {exc}")
            continue
        if r["ok"]:
            ok += 1
            probs = "; ".join(f"{p['check']}: {p['detail']}" for p in r["problems"])
            print(f"[ OK ] {f.name} -> {r['symbol']} ({r['years']} yrs to {r['last_year']}, {r['sector_type']})"
                  + (f"\n       check: {probs}" if probs else ""))
        else:
            print(f"[????] {f.name}: {r['company']} - {r['reason']} (best guess {r['best_guess']})")
    print(f"Imported {ok} of {len(files)} files.")


def cmd_import_workbook(a):
    from . import workbook
    r = workbook.import_workbook(Path(a.file).read_bytes(), keep_exports=not a.replace_exports)
    print(f"Imported {len(r['imported'])} companies; kept {len(r['merged_with_exports'])} richer Screener exports "
          f"(added reported capex to them); skipped {len(r['skipped'])}:")
    for sk in r["skipped"]:
        print(f"   {sk['symbol']}: {sk['reason']}")


def cmd_fill_prices(a):
    from . import server, workbook
    todo = [s for s in fundamentals.all_symbols() if fundamentals.needs_year_end_prices(fundamentals.load(s))]
    ok = 0
    for i, sym in enumerate(todo, 1):
        try:
            workbook.fill_prices(sym, prices.history(sym, rng="10y"))
            ok += 1
        except Exception as exc:
            print(f"[FAIL] {sym}: {exc}")
        if i % 25 == 0:
            print(f"... {i}/{len(todo)}")
    print(f"Filled year-end prices and market caps for {ok} of {len(todo)} companies.")


def cmd_autofill(a):
    from . import autofill
    if a.only:
        fn = {"gsec": autofill.update_gsec, "gnpa": autofill.fill_gnpa, "ownership": autofill.fill_ownership,
              "prices": autofill.fill_all_prices, "financials": autofill.refresh_financials}[a.only]
        res = {a.only: fn(a.symbols or None) if a.only in ("gnpa", "ownership") else fn()}
    else:
        res = autofill.run(force=True)
    print(json.dumps(res, indent=2))


def cmd_ledger(a):
    from . import recommendations
    rows = recommendations.history(a.symbol, limit=a.limit)
    if a.csv:
        Path(a.csv).write_text(recommendations.to_csv(rows), encoding="utf-8-sig")
        print(f"Wrote {len(rows)} rows to {a.csv}")
        return
    for r in rows:
        sc = "/".join("-" if r[k] is None else f"{r[k]:.0f}" for k in ("buffett", "munger", "lynch"))
        px = "-" if r["price"] is None else f"{r['price']:,.2f} ({r['price_date']})"
        print(f"{r['logged_at'][:16]}  {r['symbol']:<12} {r['reason']:<22} B/M/L {sc:<9} matches {', '.join(r['matches']) or 'none':<22} price {px}")
    if not rows:
        print("No entries yet.")


def cmd_refresh_current(a):
    from . import price_snapshot
    cfg = config.load_config()
    r = price_snapshot.refresh(cfg=cfg, benchmark_date=prices.benchmark(cfg)[-1]["date"])
    print(f"NSE closing prices dated {r['nse_date'] or 'n/a'}: {len(r['updated'])} companies updated.")
    if r["nse_error"]:
        print(f"NSE problem: {r['nse_error']}")
    for label, key in (("already had a newer price", "stale_not_overwritten"), ("no share count", "no_share_count")):
        if r.get(key):
            print(f"{len(r[key])} skipped ({label}): {', '.join(r[key])}")
    if r["yahoo_updated"]:
        print(f"Yahoo fallback updated {len(r['yahoo_updated'])}: {', '.join(r['yahoo_updated'])}")
    for sym, err in r["yahoo_failed"].items():
        print(f"[STALE] {sym}: {err}")


def cmd_latest_prices(a):
    from . import server, workbook
    r = workbook.apply_latest_prices(Path(a.file).read_text(encoding="utf-8-sig"))
    print(f"Updated {len(r['updated'])} of {r['in_file']} prices. "
          f"{len(r['skipped_no_shares'])} skipped (no share count), {r['not_yet_imported']} not yet imported.")


def cmd_daily(a):
    from . import appnotify
    try:
        res = daily.run(send_alerts=not a.no_notify)
    except Exception as exc:
        import traceback
        appnotify.program("The daily check stopped with an error",
                          f"{type(exc).__name__}: {exc}\n\nNo alerts or report were produced today. Details:\n"
                          + traceback.format_exc()[-1500:], level="error", key="daily-crash")
        raise
    print(json.dumps(res, indent=2))
    return 0 if not res["errors"] else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="india_screener", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check", help="smoke-test every data source")
    s.add_argument("symbol", nargs="?", default="INFY")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("universe"); s.add_argument("--refresh", action="store_true"); s.add_argument("--show", type=int, default=0); s.set_defaults(fn=cmd_universe)
    s = sub.add_parser("prices"); s.add_argument("symbol", type=str.upper); s.add_argument("--exchange", default="NSE")
    s.add_argument("--days", type=int, default=5); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_prices)
    s = sub.add_parser("news"); s.add_argument("symbol", type=str.upper); s.add_argument("--name"); s.add_argument("--alias", action="append")
    s.add_argument("--days", type=int, default=7); s.set_defaults(fn=cmd_news)
    s = sub.add_parser("shortlist", help="manually edit data/shortlist.json (your scoring core will overwrite it)")
    s.add_argument("action", choices=["show", "add", "remove"]); s.add_argument("symbols", nargs="*")
    s.add_argument("--exchange", default="NSE"); s.add_argument("--alias", action="append"); s.set_defaults(fn=cmd_shortlist)
    s = sub.add_parser("portfolio")
    s.add_argument("action", choices=["show", "add", "txns", "delete", "import-zerodha"])
    s.add_argument("kind", nargs="?", help="BUY/SELL/DIVIDEND/SPLIT/BONUS (for add)")
    s.add_argument("symbol", nargs="?")
    s.add_argument("--date"); s.add_argument("--qty", default="0"); s.add_argument("--price", default="0")
    s.add_argument("--fees", default="0"); s.add_argument("--amount", default="0"); s.add_argument("--ratio")
    s.add_argument("--exchange", default="NSE"); s.add_argument("--note", default="")
    s.add_argument("--file"); s.add_argument("--id", type=int); s.set_defaults(fn=cmd_portfolio)
    s = sub.add_parser("import-folder", help="import every Screener export in a folder (matched by company name)")
    s.add_argument("folder"); s.add_argument("--basis", default="consolidated", choices=["consolidated", "standalone"])
    s.set_defaults(fn=cmd_import_folder)
    s = sub.add_parser("import-workbook", help="import the combined top-250 financials workbook")
    s.add_argument("file"); s.add_argument("--replace-exports", action="store_true",
                                           help="overwrite companies already imported from Screener exports")
    s.set_defaults(fn=cmd_import_workbook)
    s = sub.add_parser("fill-prices", help="fetch year-end prices and current market caps from Yahoo for workbook companies")
    s.set_defaults(fn=cmd_fill_prices)
    s = sub.add_parser("autofill", help="refresh G-Sec yield, Gross NPA %%, promoter/pledge/FII+DII %% and year-end prices from the web")
    s.add_argument("--only", choices=["gsec", "gnpa", "ownership", "prices", "financials"])
    s.add_argument("symbols", nargs="*", type=str.upper, help="limit gnpa/ownership to these stocks"); s.set_defaults(fn=cmd_autofill)
    s = sub.add_parser("ledger", help="the permanent record of every shortlisting, alert, buy and sell with its analysis")
    s.add_argument("symbol", nargs="?", type=str.upper); s.add_argument("--limit", type=int, default=100)
    s.add_argument("--csv", help="export to this CSV file instead of printing"); s.set_defaults(fn=cmd_ledger)
    s = sub.add_parser("refresh-current-prices", help="today's closing price for every imported company from one NSE file (Yahoo fallback)")
    s.set_defaults(fn=cmd_refresh_current)
    s = sub.add_parser("latest-prices", help="refresh current price and market cap from a Ticker,Latest_Price CSV snapshot")
    s.add_argument("file"); s.set_defaults(fn=cmd_latest_prices)
    s = sub.add_parser("events"); s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_events)
    s = sub.add_parser("daily"); s.add_argument("--no-notify", action="store_true"); s.set_defaults(fn=cmd_daily)
    s = sub.add_parser("serve", help="open the dashboard in your browser")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=lambda a: __import__("india_screener.server", fromlist=["serve"]).serve(a.port, not a.no_browser))
    s = sub.add_parser("notify-test"); s.set_defaults(fn=lambda a: print(notify.send("Screener test alert", "If you see this, alerts work.") or "sent"))
    s = sub.add_parser("schedule"); s.add_argument("action", choices=["install", "remove", "show"]); s.add_argument("--time", default="18:30")
    s.set_defaults(fn=lambda a: schedule.install(a.time) if a.action == "install" else schedule.remove() if a.action == "remove" else schedule.show())
    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (portfolio.LedgerError, ValueError, net.FetchError, prices.DataError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
