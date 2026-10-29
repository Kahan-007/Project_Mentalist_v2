"""Import company financials and derive the metrics the scoring engine needs.

Input: Screener.in 'Export to Excel' workbook (sheet 'Data Sheet'), or a CSV laid out the same way
(see `template_csv`). All money values in Rs crore; shares in crore; price in Rs.
Choose the CONSOLIDATED view on Screener before exporting whenever the company has subsidiaries.
"""
import csv
import copy
import datetime as dt
import io
import math
import re
import statistics

from . import config, storage, xlsx, financial_math

SECTOR_TYPES = ("general", "bank", "nbfc", "insurance")
BASES = ("consolidated", "standalone")

LABELS = {
    "pl": {"sales": "sales", "revenue": "sales", "other income": "other_income", "depreciation": "depreciation",
           "interest": "interest", "profit before tax": "pbt", "tax": "tax", "net profit": "net_profit", "operating ebit": "operating_ebit",
           "dividend amount": "dividend"},
    "bs": {"equity share capital": "share_capital", "reserves": "reserves", "borrowings": "borrowings",
           "other liabilities": "other_liabilities", "total": "total", "net block": "net_block",
           "capital work in progress": "cwip", "investments": "investments", "other assets": "other_assets",
           "receivables": "receivables", "inventory": "inventory", "cash & bank": "cash", "cash and bank": "cash"},
    "cf": {"cash from operating activity": "cfo", "cash from investing activity": "cfi",
           "cash from financing activity": "cff", "net cash flow": "net_cash_flow",
           "capital expenditure": "capex_reported"},
    "q": {"sales": "sales", "revenue": "sales", "expenses": "expenses", "other income": "other_income",
          "depreciation": "depreciation", "interest": "interest", "profit before tax": "pbt", "tax": "tax",
          "net profit": "net_profit", "operating profit": "operating_profit"},
}
BS_EXTRA = {"no of equity shares": "shares_count", "face value": "face_value_row", "new bonus shares": "bonus_shares"}
META = {"number of shares": "shares", "face value": "face_value", "current price": "price",
        "market capitalization": "market_cap"}


class ImportError_(ValueError):
    pass


def _norm(label):
    return re.sub(r"\s+", " ", re.sub(r"[:.]", "", str(label or ""))).strip().lower()


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if math.isfinite(v) else None
    try:
        value = float(str(v).replace(",", "").strip())
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def _date(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return xlsx.excel_date(v)
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%b-%y", "%b %Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_grid(grid):
    section, dates_cols, out = None, {}, {"meta": {}, "pl": {}, "bs": {}, "cf": {}, "q": {}, "price": {}, "shares_cr": {}}
    name = None
    for row in grid:
        if not row:
            continue
        label = _norm(row[0])
        if not label:
            continue
        up = str(row[0]).upper()
        if label == "company name":
            name = next((str(x) for x in row[1:] if x not in (None, "")), None)
            continue
        if "PROFIT & LOSS" in up or "PROFIT AND LOSS" in up:
            section = "pl"; continue
        if "QUARTER" in up:
            section = "q"; continue
        if "BALANCE SHEET" in up:
            section = "bs"; continue
        if "CASH FLOW" in up:
            section = "cf"; continue
        if up.startswith("META") or up.startswith("DERIVED"):
            section = "other"; continue
        if label == "report date":
            dates_cols[section] = {i: _date(v) for i, v in enumerate(row) if i > 0 and v not in (None, "") and _date(v)}
            continue
        if label in META and section in (None, "other"):
            val = next((_num(x) for x in row[1:] if _num(x) is not None), None)
            out["meta"][META[label]] = val
            continue
        if label == "price" or label.startswith("price "):
            cols = dates_cols.get("pl", {})
            out["price"] = {d: _num(row[i]) for i, d in cols.items() if i < len(row)}
            continue
        if "adjusted equity shares" in label:
            cols = dates_cols.get("pl", {})
            out["shares_cr"] = {d: _num(row[i]) for i, d in cols.items() if i < len(row)}
            continue
        if section == "bs" and label in BS_EXTRA:
            cols = dates_cols.get("bs", {})
            out["bs"][BS_EXTRA[label]] = {d: _num(row[i]) for i, d in cols.items() if i < len(row)}
            continue
        if section in ("pl", "bs", "cf", "q") and label in LABELS[section]:
            key = LABELS[section][label]
            if key == "total" and "total" in out["bs"]:
                key = "total_assets"   # second 'Total' is the assets side; kept for a consistency check
            if key in out[section]:
                continue
            cols = dates_cols.get(section, {})
            out[section][key] = {d: _num(row[i]) for i, d in cols.items() if i < len(row)}
    years = sorted(dates_cols.get("pl", {}).values())
    if len(years) < 3 or "net_profit" not in out["pl"]:
        raise ImportError_("could not find at least 3 annual Profit & Loss columns with a 'Net profit' row. "
                           "Upload Screener's 'Export to Excel' file or the CSV template.")
    series = {}
    for sec in ("pl", "bs", "cf"):
        for key, by_date in out[sec].items():
            series[key] = [by_date.get(y) for y in years]
    series["price"] = [out["price"].get(y) for y in years]
    series["shares_cr"] = [out["shares_cr"].get(y) for y in years]
    qdates = sorted(dates_cols.get("q", {}).values())
    quarters = {"dates": qdates, **{k: [v.get(d) for d in qdates] for k, v in out["q"].items()}}
    return {"name": name, "years": years, "series": series, "meta": out["meta"], "quarters": quarters}


def parse_file(data: bytes, filename: str):
    if data[:2] == b"PK":
        sheets = xlsx.read_workbook(io.BytesIO(data))
        sheet = next((sheets[n] for n in sheets if n.strip().lower() == "data sheet"), None)
        if sheet is None:
            raise ImportError_(f"no 'Data Sheet' tab found (tabs: {', '.join(sheets)})")
        return parse_grid(sheet)
    text = data.decode("utf-8-sig", errors="replace")
    return parse_grid(list(csv.reader(io.StringIO(text))))


def save(symbol, parsed, basis, sector_type, source_name, industry=None):
    if basis not in BASES:
        raise ImportError_(f"basis must be one of {BASES}")
    if sector_type not in SECTOR_TYPES:
        raise ImportError_(f"sector type must be one of {SECTOR_TYPES}")
    existing = load(symbol) or {}
    record = {"symbol": symbol, "basis": basis, "sector_type": sector_type, "source": source_name,
              "industry": industry or existing.get("industry"),
              "imported_at": config.now_ist().isoformat(), "manual": existing.get("manual", {}),
              "manual_source": existing.get("manual_source", {}), **parsed}
    if existing.get('valuation_research'):
        record['valuation_research'] = existing['valuation_research']
    if existing:
        from .screener_fetch import merge_refresh
        merged, _ = merge_refresh(record, existing, preserve_export=False)
        record = merged or existing
    storage.write_json(config.DATA / "fundamentals" / f"{symbol}.json", record)
    return record


def merge_quarters(old, new):
    """Merge by date and field, retaining richer old rows when a provider omits them."""
    dates = sorted(set(old.get('dates', [])) | set(new.get('dates', [])))
    result = {'dates': dates}
    for key in (set(old) | set(new)) - {'dates'}:
        if not isinstance(old.get(key, new.get(key)), list):
            continue
        by_date = dict(zip(old.get('dates', []), old.get(key, [])))
        by_date.update({d: v for d, v in zip(new.get('dates', []), new.get(key, [])) if v is not None})
        result[key] = [by_date.get(d) for d in dates]
    return result


def needs_year_end_prices(rec):
    """True when any stored year lacks its year-end share price (needed by the retained-earnings test)."""
    return bool(rec) and any(v is None for v in (rec.get("series") or {}).get("price") or [None])


def load(symbol):
    return storage.read_json(config.DATA / "fundamentals" / f"{symbol}.json")


def all_symbols():
    d = config.DATA / "fundamentals"
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def set_manual(symbol, source=None, **fields):
    """source=None means the user typed it; automatic fetchers pass a label (e.g. 'screener.in Jun 2026')
    so they know which values they may refresh later without overwriting the user's own entries."""
    rec = load(symbol)
    if not rec:
        raise ImportError_(f"import {symbol}'s financials first")
    allowed = {"promoter_pct", "pledge_pct", "institutional_pct", "gnpa_pct"}
    src = rec.setdefault("manual_source", {})
    for k, v in fields.items():
        if k not in allowed:
            continue
        v = _num(v)
        if v is not None and not (0 <= v <= 100):
            raise ImportError_(f"{k} must be between 0 and 100")
        rec["manual"][k] = v
        if source:
            src[k] = source
        else:
            src.pop(k, None)
    storage.write_json(config.DATA / "fundamentals" / f"{symbol}.json", rec)
    return rec


# ---------------------------------------------------------------- derived metrics

def _get(s, key):
    return s.get(key) or []


def _at(lst, i):
    return lst[i] if 0 <= i < len(lst) else None


def _ok(*xs):
    return all(x is not None for x in xs)


def cagr(first, last, years):
    if not _ok(first, last) or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def metrics(rec, operating=False):
    s, years = rec["series"], rec["years"]
    n = len(years)
    sales, np_, pbt, intr = (_get(s, k) for k in ("sales", "net_profit", "pbt", "interest"))
    dep, div = _get(s, "depreciation"), _get(s, "dividend")
    cap, res, debt = _get(s, "share_capital"), _get(s, "reserves"), _get(s, "borrowings")
    nb, cwip, total = _get(s, "net_block"), _get(s, "cwip"), _get(s, "total")
    cfo, recv, inv, cash = _get(s, "cfo"), _get(s, "receivables"), _get(s, "inventory"), _get(s, "cash")
    other_inc, shares = _get(s, "other_income"), _get(s, "shares_cr")

    eq = [(_at(cap, i) + _at(res, i)) if _ok(_at(cap, i), _at(res, i)) else None for i in range(n)]
    ce = [(eq[i] + debt[i]) if _ok(eq[i], _at(debt, i)) else None for i in range(n)]
    ebit = [(_at(pbt, i) + intr[i]) if _ok(_at(pbt, i), _at(intr, i)) else None for i in range(n)]

    def avg_base(lst, i):
        cur, prev = _at(lst, i), _at(lst, i - 1)
        if cur is None:
            return None
        return (cur + prev) / 2 if prev is not None else cur

    def ratio(num, den):
        return [num[i] / den[i] if _ok(_at(num, i), _at(den, i)) and den[i] > 0 else None for i in range(n)]

    roe = [(_at(np_, i) / avg_base(eq, i)) if _ok(_at(np_, i), avg_base(eq, i)) and avg_base(eq, i) > 0 else None for i in range(n)]
    roce = [(ebit[i] / avg_base(ce, i)) if _ok(ebit[i], avg_base(ce, i)) and avg_base(ce, i) > 0 else None for i in range(n)]
    roa = [(_at(np_, i) / avg_base(total, i)) if _ok(_at(np_, i), avg_base(total, i)) and avg_base(total, i) > 0 else None for i in range(n)]
    operating_data = financial_math.operating_returns(rec)
    roic = operating_data['roic']
    if operating:
        roce = operating_data['roce']
        ebit = operating_data['ebit']
    capex_est = [(_at(nb, i) - _at(nb, i - 1) + _at(cwip, i) - _at(cwip, i - 1) + _at(dep, i))
                 if i > 0 and _ok(_at(nb, i), _at(nb, i - 1), _at(dep, i), _at(cwip, i), _at(cwip, i - 1)) else None for i in range(n)]
    # Prefer reported cash spent on fixed assets (the standard FCF definition). The balance-sheet estimate
    # is the fallback; it also counts acquired intangibles, so it overstates capex for acquisitive firms.
    capex_rep = _get(s, "capex_reported")
    capex = [(_at(capex_rep, i) if _at(capex_rep, i) is not None else capex_est[i]) for i in range(n)]
    capex = [v if financial_math.finite(v) and v >= 0 else None for v in capex]
    capex_sources = ['reported' if _at(capex_rep,i) is not None and capex[i] is not None else
                     'estimated from balance sheet' if capex[i] is not None else 'unavailable' for i in range(n)]
    capex_source = capex_sources[0] if len(set(capex_sources)) == 1 else 'mixed: see per-period sources'

    fcf = [(_at(cfo, i) - capex[i]) if _ok(_at(cfo, i), capex[i]) else None for i in range(n)]
    eps = ratio(np_, shares)
    bvps = ratio(eq, shares)

    meta = rec.get("meta", {})
    price = meta.get("price") or _at(_get(s, "price"), n - 1)
    shares_now, shares_source = financial_math.current_shares(rec)
    mcap = meta.get("market_cap") or (price * shares_now if _ok(price, shares_now) else None)

    def window(lst, k):
        vals = [v for v in lst[-k:] if v is not None]
        return vals

    spans = {}

    def span_cagr(lst, k, name, min_span):
        """CAGR over (up to) the last k years using the endpoints available in that window.
        Returns None if fewer than `min_span` years separate the endpoints. The span used is recorded."""
        idx = [i for i in range(max(0, n - k - 1), n) if _at(lst, i) is not None]
        if len(idx) < 2 or idx[-1] - idx[0] < min_span:
            return None
        try:
            first_date,last_date = (dt.date.fromisoformat(years[j]) for j in (idx[0],idx[-1]))
            span = ((last_date.year-first_date.year)*12+last_date.month-first_date.month)/12
        except (ValueError,TypeError):
            return None
        if span < min_span:
            return None
        spans[name] = span
        first, last_v = lst[idx[0]], lst[idx[-1]]
        if last_v <= 0:
            return -1.0          # ended at a loss: growth test fails outright
        if first <= 0:
            return None          # growth from a loss base is undefined, not zero growth
        return cagr(first, last_v, span)

    def paired_ratio(a, b, k, minimum=3):
        pairs = [(_at(a, i), _at(b, i)) for i in range(max(0, n-k), n) if _ok(_at(a, i), _at(b, i))]
        denominator = sum(y for _, y in pairs)
        return sum(x for x, _ in pairs) / denominator if len(pairs) >= minimum and denominator > 0 else None

    last = n - 1
    m = {
        "years": years, "n_years": n, "capex_source": capex_source, "capex_sources": capex_sources,
        "operating_returns": operating_data, "share_evidence": financial_math.share_evidence(rec),
        "roe": roe, "roce": roce, "roic": roic, "roa": roa, "eps": eps, "bvps": bvps, "fcf": fcf, "capex": capex, "equity": eq,
        "ebit_margin": [ebit[i] / sales[i] if _ok(ebit[i], _at(sales, i)) and sales[i] > 0 else None for i in range(n)],
        "de_ratio": [debt[i] / eq[i] if eq[i] and eq[i] > 0 and _at(debt, i) is not None else None for i in range(n)],
        "interest_cover": (ebit[last] / intr[last]) if _ok(ebit[last], _at(intr, last)) and intr[last] > 0 else
                          (math.inf if _at(intr, last) == 0 and ebit[last] is not None and ebit[last] > 0 else None),
        "price": price, "market_cap": mcap, "shares_cr": shares_now, "shares_source":shares_source,
        "net_profit_last": _at(np_, last),
        "pe": (mcap / np_[last]) if _ok(mcap, _at(np_, last)) and np_[last] > 0 else None,
        "pb": (mcap / eq[last]) if _ok(mcap, eq[last]) and eq[last] > 0 else None,
        "dividend_yield": (div[last] / mcap) if _ok(_at(div, last), mcap) and mcap > 0 else None,
        "payout": (div[last] / np_[last]) if _ok(_at(div, last), _at(np_, last)) and np_[last] > 0 else None,
        "debt": _at(debt, last), "cash": _at(cash, last), "investments": _at(_get(s, "investments"), last),
        "eps_cagr_5": span_cagr(eps, 5, "eps_cagr_5", 3), "eps_cagr_10": span_cagr(eps, 10, "eps_cagr_10", 6),
        "sales_cagr_5": span_cagr(sales, 5, "sales_cagr_5", 3), "sales_cagr_10": span_cagr(sales, 10, "sales_cagr_10", 6),
        "bvps_cagr_5": span_cagr(bvps, 5, "bvps_cagr_5", 3),
        "recv_cagr_5": span_cagr(recv, 5, "recv_cagr_5", 3), "inv_cagr_5": span_cagr(inv, 5, "inv_cagr_5", 3),
        "shares_cagr_5": span_cagr(shares, 5, "shares_cagr_5", 3),
        "cash_conversion": paired_ratio(cfo, np_, 5),
        "capex_to_cfo": paired_ratio(capex, cfo, 5),
        "fcf_avg_3": statistics.fmean(window(fcf, 3)) if len(window(fcf, 3)) == 3 else None,
        "fcf_median_all": statistics.median(window(fcf, 10)) if len(window(fcf, 10)) >= 3 else None,
        "fcf_conversion": (lambda pr: sum(f for f, _ in pr) / sum(p for _, p in pr)
                           if len(pr) >= 3 and sum(p for _, p in pr) > 0 else None)(
            [(fcf[i], np_[i]) for i in range(max(0, n - 5), n) if _ok(fcf[i], _at(np_, i))]),
        "losses": sum(1 for v in np_ if v is not None and v <= 0),
        "np_years": sum(1 for v in np_ if v is not None),
        "other_income_share": paired_ratio(other_inc, pbt, 3),
        "tax_rate_avg": paired_ratio(_get(s, "tax"), pbt, 5),
    }
    # Trailing twelve months from the last 4 quarters, if they run past the last annual report.
    q = rec.get("quarters") or {}
    qd, qnp, qs = q.get("dates", []), q.get("net_profit", []), q.get("sales", [])
    m["ttm"] = None
    contiguous, m['ttm_unavailable_reason'] = financial_math.quarterly_sequence(q)
    if contiguous and qd[-1] > years[-1]:
        m["ttm"] = {"to": qd[-1], "net_profit": sum(qnp[-4:]),
                    "sales": sum(qs[-4:]) if len(qs) == len(qd) and all(financial_math.finite(v) for v in qs[-4:]) else None}
        if mcap and m["ttm"]["net_profit"] > 0:
            m["pe"] = mcap / m["ttm"]["net_profit"]
            m["pe_basis"] = f"trailing 12 months to {qd[-1]}"
        elif m["ttm"]["net_profit"] <= 0:
            m["pe"] = None
            m["pe_basis"] = "loss over trailing 12 months"
    m.setdefault("pe_basis", f"financial year {years[-1]}")
    m["spans"] = spans
    # Retained-earnings ('$1') test over up to 5 years, needs price and shares at both ends.
    k = min(5, n - 1)
    p0, s0, p1, s1 = _at(_get(s, "price"), last - k), _at(shares, last - k), _at(_get(s, "price"), last), _at(shares, last)
    retained = [(_at(np_, i) - _at(div, i)) for i in range(last - k + 1, last + 1) if _ok(_at(np_, i), _at(div, i))]
    if _ok(p0, s0, p1, s1) and len(retained) == k and retained and sum(retained) > 0:
        m["retained_test"] = (p1 * s1 - p0 * s0) / sum(retained)
    else:
        m["retained_test"] = None
    return m


def template_csv(years=("2021-03-31", "2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31")):
    rows = [["COMPANY NAME", "Example Ltd"], ["META"], ["Current Price", ""], ["Market Capitalization", ""],
            ["Number of shares", ""], ["PROFIT & LOSS"], ["Report Date", *years]]
    rows += [[l] for l in ("Sales", "Other Income", "Depreciation", "Interest", "Profit before tax", "Tax",
                           "Net profit", "Dividend Amount")]
    rows += [["BALANCE SHEET"], ["Report Date", *years]]
    rows += [[l] for l in ("Equity Share Capital", "Reserves", "Borrowings", "Other Liabilities", "Total", "Net Block",
                           "Capital Work in Progress", "Investments", "Other Assets", "Receivables", "Inventory", "Cash & Bank")]
    rows += [["CASH FLOW"], ["Report Date", *years], ["Cash from Operating Activity"], ["Cash from Investing Activity"],
             ["Cash from Financing Activity"], ["PRICE"], ["DERIVED"], ["Adjusted Equity Shares in Cr"]]
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()
