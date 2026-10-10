"""Transaction ledger with exact (Decimal) FIFO accounting.

Kinds:
  BUY / SELL   quantity, price per share, fees (brokerage+STT+charges, total for the trade)
  DIVIDEND     amount = total cash received
  SPLIT        ratio_num:ratio_den new:old   (1 share -> 5 shares is 5:1)
  BONUS        ratio_num:ratio_den bonus:held (1 bonus for every 2 held is 1:2)
Buy fees are capitalised into cost; sell fees reduce proceeds.
Splits/bonuses rescale open lots and keep total cost unchanged.
"""
import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation

from . import db as dbm
from .universe import SYMBOL_RE

KINDS = {"BUY", "SELL", "DIVIDEND", "SPLIT", "BONUS"}


class LedgerError(ValueError):
    pass


def D(x, name="value"):
    try:
        d = Decimal(str(x).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise LedgerError(f"{name} must be a number, got {x!r}")
    if not d.is_finite():
        raise LedgerError(f"{name} must be finite")
    return d


def _date(s):
    try:
        return dt.date.fromisoformat(str(s).strip()[:10]).isoformat()
    except ValueError:
        raise LedgerError(f"date must be YYYY-MM-DD, got {s!r}")


def _symbol(s):
    s = str(s).strip().upper().removesuffix(".NS").removesuffix(".BO")
    if not SYMBOL_RE.match(s):
        raise LedgerError(f"invalid symbol {s!r}")
    return s


def add(kind, symbol, trade_date, quantity=0, price=0, fees=0, amount=0, ratio=None,
        exchange="NSE", note="", external_id=None, path=None):
    kind = kind.upper()
    if kind not in KINDS:
        raise LedgerError(f"kind must be one of {sorted(KINDS)}")
    symbol, trade_date = _symbol(symbol), _date(trade_date)
    q, p, f, a = D(quantity, "quantity"), D(price, "price"), D(fees, "fees"), D(amount, "amount")
    num = den = None
    if kind in ("BUY", "SELL"):
        if q <= 0 or q != q.to_integral_value():
            raise LedgerError("quantity must be a positive whole number")
        if p <= 0 or f < 0:
            raise LedgerError("price must be > 0 and fees >= 0")
    elif kind == "DIVIDEND":
        if a <= 0:
            raise LedgerError("dividend amount must be > 0")
    else:
        try:
            num, den = (int(x) for x in str(ratio).split(":"))
        except (ValueError, AttributeError):
            raise LedgerError("ratio must look like 5:1")
        if num <= 0 or den <= 0:
            raise LedgerError("ratio parts must be positive")
    with dbm.connect(path) as db:
        cur = db.execute(
            "INSERT INTO transactions(trade_date,symbol,exchange,kind,quantity,price,fees,amount,ratio_num,ratio_den,external_id,note)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date, symbol, exchange.upper(), kind, str(q), str(p), str(f), str(a), num, den, external_id, note))
        new_id = cur.lastrowid
    try:
        positions(path)   # replay to reject e.g. selling more than held
    except LedgerError:
        with dbm.connect(path) as db:
            db.execute("DELETE FROM transactions WHERE id=?", (new_id,))
        raise
    return new_id


def delete(txn_id, path=None):
    with dbm.connect(path) as db:
        return db.execute("DELETE FROM transactions WHERE id=?", (int(txn_id),)).rowcount


def transactions(path=None):
    with dbm.connect(path) as db:
        return [dict(r) for r in db.execute("SELECT * FROM transactions ORDER BY trade_date, id")]


def positions(path=None):
    """Replay the ledger. Returns {symbol: {...}} including closed positions (quantity 0)."""
    book = {}
    for t in transactions(path):
        s = book.setdefault(t["symbol"], {"symbol": t["symbol"], "exchange": t["exchange"], "lots": [],
                                          "realised": Decimal(0), "dividends": Decimal(0), "fees": Decimal(0)})
        kind, q, p, f = t["kind"], Decimal(t["quantity"]), Decimal(t["price"]), Decimal(t["fees"])
        if kind == "BUY":
            s["lots"].append({"date": t["trade_date"], "qty": q, "cost": q * p + f})
            s["fees"] += f
        elif kind == "SELL":
            held = sum(l["qty"] for l in s["lots"])
            if q > held:
                raise LedgerError(f"{t['symbol']} {t['trade_date']}: selling {q} but only {held} held")
            proceeds, remaining, cost = q * p - f, q, Decimal(0)
            while remaining > 0:
                lot = s["lots"][0]
                take = min(remaining, lot["qty"])
                part = lot["cost"] * take / lot["qty"]
                cost += part
                lot["qty"] -= take
                lot["cost"] -= part
                remaining -= take
                if lot["qty"] == 0:
                    s["lots"].pop(0)
            s["realised"] += proceeds - cost
            s["fees"] += f
        elif kind == "DIVIDEND":
            s["dividends"] += Decimal(t["amount"])
        else:
            factor = (Decimal(t["ratio_num"]) / Decimal(t["ratio_den"]) if kind == "SPLIT"
                      else 1 + Decimal(t["ratio_num"]) / Decimal(t["ratio_den"]))
            for lot in s["lots"]:
                new_q = lot["qty"] * factor
                if new_q != new_q.to_integral_value():
                    raise LedgerError(f"{t['symbol']} {kind} {t['ratio_num']}:{t['ratio_den']} gives fractional shares "
                                      f"({new_q}) on a lot; record the fractional cash as a SELL instead")
                lot["qty"] = new_q
    out = {}
    for sym, s in book.items():
        qty = sum(l["qty"] for l in s["lots"])
        invested = sum(l["cost"] for l in s["lots"])
        out[sym] = {"symbol": sym, "exchange": s["exchange"], "quantity": qty, "invested": invested,
                    "avg_cost": (invested / qty) if qty else Decimal(0),
                    "realised": s["realised"], "dividends": s["dividends"], "fees": s["fees"],
                    "first_open_lot": s["lots"][0]["date"] if s["lots"] else None,
                    "lots": [{"date": l["date"], "qty": l["qty"], "cost_per_share": l["cost"] / l["qty"]} for l in s["lots"]]}
    return out


def holdings(path=None):
    return {k: v for k, v in positions(path).items() if v["quantity"] > 0}


def import_zerodha_tradebook(csv_text, path=None):
    """Import a Zerodha Console tradebook CSV (Reports -> Tradebook -> Equity -> download CSV).
    De-duplicated by trade_id, so re-importing overlapping files is safe. Fees are not in the tradebook;
    add them afterwards if you want exact cost (contract notes have them)."""
    rows = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))))
    if not rows:
        raise LedgerError("tradebook is empty")
    cols = {c.strip().lower(): c for c in rows[0].keys()}
    need = ["symbol", "trade_date", "trade_type", "quantity", "price", "trade_id"]
    missing = [c for c in need if c not in cols]
    if missing:
        raise LedgerError(f"tradebook missing columns {missing}; found {list(cols)}")
    get = lambda r, c: (r[cols[c]] or "").strip()
    rows.sort(key=lambda r: (get(r, "trade_date")[:10],
                             get(r, "order_execution_time") if "order_execution_time" in cols else "",
                             0 if get(r, "trade_type").lower() == "buy" else 1))
    added = skipped = 0
    symbols = set()
    for r in rows:
        seg = get(r, "segment").upper() if "segment" in cols else "EQ"
        if seg and seg != "EQ":
            skipped += 1
            continue
        ext = "zerodha:" + get(r, "exchange") + ":" + get(r, "trade_id") if "exchange" in cols else "zerodha:" + get(r, "trade_id")
        with dbm.connect(path) as db:
            if db.execute("SELECT 1 FROM transactions WHERE external_id=?", (ext,)).fetchone():
                skipped += 1
                continue
        add(get(r, "trade_type").upper(), get(r, "symbol"), get(r, "trade_date"), quantity=get(r, "quantity"),
            price=get(r, "price"), exchange=(get(r, "exchange") or "NSE") if "exchange" in cols else "NSE",
            external_id=ext, note="imported from Zerodha tradebook", path=path)
        added += 1
        symbols.add(get(r, "symbol").upper())
    return {"added": added, "skipped": skipped, "symbols": sorted(symbols)}
