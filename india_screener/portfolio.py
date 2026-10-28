"""Transaction ledger with exact (Decimal) FIFO accounting.

Kinds:
  BUY / SELL   quantity, price per share, fees (brokerage+STT+charges, total for the trade)
  DIVIDEND     amount = total cash received
  SPLIT        ratio_num:ratio_den new:old   (1 share -> 5 shares is 5:1)
  BONUS        ratio_num:ratio_den bonus:held (1 bonus for every 2 held is 1:2)
Buy fees are capitalised into cost; sell fees reduce proceeds.
Splits/bonuses rescale lots, preserving total basis. Fractional aggregate entitlements require
actual cash proceeds in amount; allocated basis is realized and the remaining basis is preserved.
"""
import csv
import datetime as dt
import io
import json
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


def _values(kind, symbol, trade_date, quantity=0, price=0, fees=0, amount=0, ratio=None,
            exchange="NSE", note="", external_id=None):
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
        if a < 0 or f < 0:
            raise LedgerError("fractional settlement and fees must be nonnegative")
    if exchange.upper() not in ("NSE", "BSE"):
        raise LedgerError("exchange must be NSE or BSE")
    return (trade_date, symbol, exchange.upper(), kind, str(q), str(p), str(f), str(a), num, den, external_id, note)


_FIELDS = "trade_date,symbol,exchange,kind,quantity,price,fees,amount,ratio_num,ratio_den,external_id,note"


def _rows(db):
    # Preserve historical same-day insertion order; never invent buy-before-sell ordering.
    return [dict(r) for r in db.execute("SELECT * FROM transactions ORDER BY trade_date, id")]


def _audit(db, action, txn_id, before=None, after=None):
    db.execute("INSERT INTO ledger_audit(action,transaction_id,before_json,after_json) VALUES(?,?,?,?)",
               (action, txn_id, json.dumps(before), json.dumps(after)))


def _insert(db, values):
    cur = db.execute(f"INSERT INTO transactions({_FIELDS}) VALUES({','.join('?' for _ in values)})", values)
    _audit(db, "add", cur.lastrowid, after=dict(zip(_FIELDS.split(','), values)))
    return cur.lastrowid


def add(kind, symbol, trade_date, quantity=0, price=0, fees=0, amount=0, ratio=None,
        exchange="NSE", note="", external_id=None, path=None):
    values = _values(kind, symbol, trade_date, quantity, price, fees, amount, ratio, exchange, note, external_id)
    with dbm.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        new_id = _insert(db, values)
        _replay(_rows(db))
        return new_id


def delete(txn_id, path=None):
    with dbm.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM transactions WHERE id=?", (int(txn_id),)).fetchone()
        if old is None:
            return 0
        db.execute("DELETE FROM transactions WHERE id=?", (int(txn_id),))
        _replay(_rows(db))
        _audit(db, "delete", int(txn_id), before=dict(old))
        return 1


def correct(txn_id, path=None, **changes):
    """Correct in place, preserving same-day order and an audit trail; invalid replay rolls back."""
    allowed = set(_FIELDS.split(',')) - {'ratio_num', 'ratio_den'} | {'ratio'}
    if set(changes) - allowed:
        raise LedgerError("unsupported transaction fields")
    with dbm.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM transactions WHERE id=?", (int(txn_id),)).fetchone()
        if old is None:
            raise LedgerError("transaction not found")
        data = {k: old[k] for k in _FIELDS.split(',') if k not in ('ratio_num','ratio_den')}
        data['ratio'] = f"{old['ratio_num']}:{old['ratio_den']}"
        data.update(changes)
        values = _values(**data)
        db.execute("UPDATE transactions SET " + ','.join(k+'=?' for k in _FIELDS.split(',')) + " WHERE id=?",
                   (*values, int(txn_id)))
        _replay(_rows(db))
        _audit(db, "correct", int(txn_id), dict(old), dict(zip(_FIELDS.split(','), values)))
        return int(txn_id)


def transactions(path=None):
    with dbm.connect(path) as db:
        return _rows(db)


def positions(path=None):
    """Replay the ledger. Returns {symbol: {...}} including closed positions (quantity 0)."""
    return _replay(transactions(path))


def _replay(rows):
    book = {}
    for t in rows:
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
            held = sum((lot['qty'] for lot in s['lots']), Decimal(0))
            entitlement = held * factor
            fractional = entitlement % 1
            settlement = Decimal(t['amount'])
            if fractional and settlement <= 0:
                raise LedgerError(f"{t['symbol']}: fractional entitlement {fractional}; supply actual cash settlement amount")
            if not fractional and (settlement or f):
                raise LedgerError("cash settlement requires a fractional entitlement")
            total_cost = sum((lot['cost'] for lot in s['lots']), Decimal(0))
            retained = entitlement - fractional
            disposed_cost = total_cost * fractional / entitlement if entitlement else Decimal(0)
            qty_left, cost_left = retained, total_cost - disposed_cost
            for i, lot in enumerate(s['lots']):
                weight = lot['qty'] / held
                lot['qty'] = retained * weight if i < len(s['lots'])-1 else qty_left
                lot['cost'] = lot['cost'] * retained / entitlement if i < len(s['lots'])-1 else cost_left
                qty_left -= lot['qty']; cost_left -= lot['cost']
            s['lots'] = [lot for lot in s['lots'] if lot['qty'] > 0]
            s['realised'] += settlement - f - disposed_cost
            s['fees'] += f
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
    with dbm.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        for r in rows:
            seg = get(r, "segment").upper() if "segment" in cols else "EQ"
            if seg and seg != "EQ":
                skipped += 1
                continue
            trade_id = get(r, 'trade_id')
            if not trade_id:
                raise LedgerError("trade_id must not be blank")
            ext = "zerodha:" + get(r, "exchange") + ":" + trade_id if "exchange" in cols else "zerodha:" + trade_id
            if db.execute("SELECT 1 FROM transactions WHERE external_id=?", (ext,)).fetchone():
                skipped += 1
                continue
            values = _values(get(r, "trade_type").upper(), get(r, "symbol"), get(r, "trade_date"),
                             quantity=get(r, "quantity"), price=get(r, "price"),
                             exchange=(get(r, "exchange") or "NSE") if "exchange" in cols else "NSE",
                             external_id=ext, note="imported from Zerodha tradebook")
            _insert(db, values)
            added += 1
            symbols.add(get(r, "symbol").upper())
        _replay(_rows(db))
    return {"added": added, "skipped": skipped, "symbols": sorted(symbols)}
