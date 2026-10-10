"""The screening universe: NSE's Nifty LargeMidcap 250 constituent list (Nifty 100 + Nifty Midcap 150).

Used for: which companies to screen each quarter, import progress, suggested business type
(bank / NBFC / insurer / general), industry peer groups, matching uploaded Screener files to symbols,
and ranking by market cap for SEBI large/mid classification.
Update it when NSE rebalances (March and September) by uploading the new CSV from niftyindices.com.
"""
import csv
import io
import re
from pathlib import Path

from . import config

BUNDLED = Path(__file__).resolve().parent / "reference" / "ind_niftylargemidcap250list.csv"
USER_COPY = lambda: config.DATA / "index250.csv"

# Judgement calls where the name alone misleads. Everything else follows the rules in suggest_type().
TYPE_OVERRIDES = {
    "MFSL": "insurance",            # holding company of Axis Max Life Insurance
    "RECLTD": "nbfc", "PFC": "nbfc", "IRFC": "nbfc", "HUDCO": "nbfc", "IREDA": "nbfc",
    "SBICARD": "nbfc", "JIOFIN": "nbfc", "BAJAJFINSV": "nbfc",
    "AIIL": "nbfc", "BAJAJHLDNG": "nbfc", "TATAINVEST": "nbfc",   # investment holding companies: value on book
    "360ONE": "general", "MOTILALOFS": "general", "GROWW": "general", "PAYTM": "general",
    "POLICYBZR": "general", "BSE": "general", "MCX": "general", "CRISIL": "general",
}


def parse(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    need = {"Company Name", "Industry", "Symbol", "ISIN Code"}
    if not rows or not need <= set(k.strip() for k in rows[0]):
        raise ValueError(f"not an NSE index constituent list (need columns {sorted(need)})")
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items()}
        out.append({"symbol": r["Symbol"], "name": r["Company Name"], "industry": r["Industry"],
                    "isin": r["ISIN Code"], "series": r.get("Series", "EQ"),
                    "suggested_type": suggest_type(r["Symbol"], r["Company Name"], r["Industry"])})
    if len(out) < 200:
        raise ValueError(f"only {len(out)} companies in the file; expected about 250")
    return out


def suggest_type(symbol, name, industry):
    if symbol in TYPE_OVERRIDES:
        return TYPE_OVERRIDES[symbol]
    if industry != "Financial Services":
        return "general"
    n = name.lower()
    if re.search(r"\bbank\b", n):
        return "bank"
    if re.search(r"insurance|assurance", n):
        return "insurance"
    if re.search(r"asset management|\bamc\b|exchange|depository|ratings?\b", n):
        return "general"
    if re.search(r"financ|capital|credit|housing|investment|leasing", n):
        return "nbfc"
    return "general"


def load():
    path = USER_COPY() if USER_COPY().exists() else BUNDLED
    return parse(path.read_text(encoding="utf-8-sig"))


def save_user_list(text):
    recs = parse(text)
    config.ensure_dirs()
    USER_COPY().write_text(text.lstrip("\ufeff"), encoding="utf-8")
    return recs


def by_symbol():
    return {r["symbol"]: r for r in load()}


# ---------------------------------------------------------------- matching Screener files to symbols

_ABBR = {"corpn": "corporation", "corp": "corporation", "co": "company", "coml": "commercial", "inds": "industries",
         "ind": "industries", "intl": "international", "engg": "engineering", "fin": "finance", "hsg": "housing",
         "invest": "investment", "svcs": "services", "pharma": "pharmaceuticals", "tech": "technologies"}
_DROP = {"ltd", "limited", "the", "of", "and", "pvt", "private", "india", "company"}


def _tokens(name):
    n = name.lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    toks = [_ABBR.get(t, t) for t in n.split()]
    # 'O N G C' style initials -> 'ongc'
    joined, buf = [], []
    for t in toks:
        if len(t) == 1:
            buf.append(t)
            continue
        if buf:
            joined.append("".join(buf)); buf = []
        joined.append(t)
    if buf:
        joined.append("".join(buf))
    return [t for t in joined if t not in _DROP]


def _initials(name):
    return "".join(t[0] for t in _tokens(name) if t)


def match(company_name, file_name="", universe=None):
    """Best symbol for a Screener 'COMPANY NAME' (or file name). Returns (symbol, confidence 0-1, runner_up)."""
    universe = universe if universe is not None else load()
    probe = _tokens(company_name or "") or _tokens(Path(file_name).stem.replace("_", " "))
    if not probe:
        return None, 0.0, None
    pset = set(probe)
    scored = []
    for r in universe:
        toks = _tokens(r["name"])
        tset = set(toks)
        if not tset:
            continue
        exact = pset & tset
        # Screener truncates long names ('Power Grid Corpn', 'SBI Life Insuran'): allow prefix matches.
        fuzzy = sum(1 for p in pset - exact
                    if any((t.startswith(p) or p.startswith(t)) and min(len(p), len(t)) >= 4 for t in tset - exact))
        score = (len(exact) + 0.8 * fuzzy) / max(len(pset), len(tset))
        if probe == [r["symbol"].lower()] or "".join(probe) == r["symbol"].lower() or "".join(probe) == _initials(r["name"]):
            score = max(score, 0.95)
        scored.append((score, r["symbol"]))
    scored.sort(reverse=True)
    if not scored or scored[0][0] <= 0:
        return None, 0.0, None
    best, runner = scored[0], (scored[1] if len(scored) > 1 else (0, None))
    confidence = best[0] if best[0] - runner[0] >= 0.15 else best[0] * 0.5   # ambiguous -> low confidence
    return best[1], round(confidence, 2), runner[1]
