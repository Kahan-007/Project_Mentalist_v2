"""Buffett / Munger / Lynch percentage-match scores for Indian companies.

Each philosophy is a list of weighted criteria. Each criterion scores 0..1 (linear between a 'zero'
and a 'full marks' point, so near-misses earn partial credit) or None when the data isn't available.
  score    = sum(weight * s) / sum(weight) over criteria with data            (0-100%)
  coverage = weight with data / total weight                                  (how much of the test ran)
A score with coverage below `min_coverage` is flagged, never silently trusted.
Banks, NBFCs and insurers use a separate criteria set (no debt/equity, capex, inventory or FCF logic).

Every threshold here is a judgement about the philosophy, written out so you can disagree with it.
"""
import math
import re
import statistics
import datetime as dt

from . import config

DEFAULT_SETTINGS = {
    "risk_free": 0.065,          # 10Y G-Sec yield. UPDATE to the current yield in Settings.
    "equity_premium": 0.03,      # discount rate = risk_free + premium
    "terminal_growth": 0.05,     # long-run nominal growth after the explicit period
    "max_stage1_growth": 0.15,
    "pass_score": 65.0,          # a philosophy 'matches' at or above this %
    "min_coverage": 0.70,
    "large_cap_min_cr": 100000,  # approximate SEBI/AMFI cut-offs in Rs crore; update from AMFI's latest list
    "mid_cap_min_cr": 33000,
    # Price discipline for the Buy/Sell signals (not the scores): above this multiple of the conservative value
    # estimate a held stock shows Sell and a shortlisted one can't show Buy. 0 = off.
    "price_discipline": 2.0,
    "entry_margin_of_safety": -1.0,  # -1 preserves legacy entry rule; otherwise require 1-price/value >= this
}


def valuation(m, rec, st, cycle_policy="legacy"):
    """Conservative value estimate (Rs cr) and price-to-value multiple: DCF for companies, justified P/B for
    lenders and insurers. multiple is None when no positive value can be estimated (e.g. negative free cash flow)."""
    if rec["sector_type"] != "general":
        value, note = justified_pb_value(m, st)
        method = "justified price-to-book"
    else:
        value, note = dcf_value(m, st, cyclical=lynch_category(m, rec, st, cycle_policy) == "cyclical")
        method = "discounted free cash flow"
    mult = (m["market_cap"] / value) if value and value > 0 and m.get("market_cap") else None
    return {"value_cr": value, "multiple": mult, "method": method, "note": note,
            "model_version":"legacy-screening-v1" if cycle_policy == "legacy" else "legacy-screening-cycle-v1",
            "production":cycle_policy == "legacy",
            "limitation": 'Approximate screening valuation; cash-flow classification and discount-rate calibration need review.'
            if rec['sector_type']=='general' else 'Simplified book-value model; not a sector-specific appraisal.'}


def sensitivity(rec, st=None):
    """Assumption sensitivity, never probabilities, price targets or replacement signals."""
    st = st or settings()
    m = fundamentals_metrics(rec, None, st)
    rows = []
    for label, shift in [('higher required return',.02),('current assumptions',0),('lower required return',-.02)]:
        altered = {**st, 'equity_premium':max(0, st['equity_premium']+shift)}
        value = valuation(m,rec,altered)
        rows.append({'scenario':label, 'discount_rate':altered['risk_free']+altered['equity_premium']+(0 if rec['sector_type']=='general' else .01),
                     'value_cr':value['value_cr'],'value_per_share':value['value_cr']/m['shares_cr'] if value['value_cr'] is not None and m.get('shares_cr') else None,
                     'margin_of_safety':margin_of_safety(value['value_cr'],m['market_cap']), 'assumptions':value['note']})
    return {'symbol':rec['symbol'],'scenarios':rows, 'limitation':valuation(m,rec,st)['limitation'],
            'note':'Discount-rate sensitivity of the existing model, with growth caps applied by that model. These are not calibrated bear/base/bull forecasts and do not change the active signal.'}


def settings():
    cfg = config.load_config()
    return {**DEFAULT_SETTINGS, **cfg.get("scoring", {})}


def linear(x, zero_at, full_at):
    """0 at zero_at, 1 at full_at, linear between, clamped. Works in either direction."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if full_at == zero_at:
        return 1.0 if x >= full_at else 0.0
    return max(0.0, min(1.0, (x - zero_at) / (full_at - zero_at)))


def pct(x, d=1):
    return "n/a" if x is None else ("inf" if x == math.inf else f"{x * 100:.{d}f}%")


def num(x, d=2):
    return "n/a" if x is None else ("inf" if x == math.inf else f"{x:.{d}f}")


def g_text(m, key):
    v = m.get(key)
    span = (m.get("spans") or {}).get(key)
    return pct(v) + (f" over {span}y" if v is not None and span else "")


def lenders(rec):
    return rec["sector_type"] in ("bank", "nbfc")


def C(key, label, weight, score, value, rule):
    return {"key": key, "label": label, "weight": weight, "score": score, "value": value, "rule": rule}


def _vals(lst, k=None):
    v = [x for x in (lst[-k:] if k else lst) if x is not None]
    return v


def size_bucket(mcap, st, rank=None):
    """SEBI: ranks 1-100 by market cap = large, 101-250 = mid, rest = small. When the rank among the
    imported top-250 universe is known it is used; otherwise the rupee cut-offs from Settings."""
    if rank is not None:
        return "large" if rank <= 100 else "mid" if rank <= 250 else "small"
    if mcap is None:
        return None
    return "large" if mcap >= st["large_cap_min_cr"] else "mid" if mcap >= st["mid_cap_min_cr"] else "small"


# ------------------------------------------------------------------ valuation

def dcf_value(m, st, cyclical=False):
    """Two-stage DCF on normalised free cash flow (capex estimated from the balance sheet).
    Base = average of the last 3 years, or for cyclicals the median across all years (so a boom
    isn't capitalised). Equity value = PV(cash flows) - debt + cash. Returns (value_cr, notes)."""
    base = m.get("fcf_median_all") if cyclical else m.get("fcf_avg_3")
    if base is None:
        return None, "free cash flow unavailable"
    if base <= 0:
        return 0.0, "average free cash flow is not positive"
    if m.get('debt') is None or m.get('cash') is None:
        return None, 'debt or cash unavailable; equity value cannot be estimated'
    g_hist = [g for g in (m.get("eps_cagr_10"), m.get("eps_cagr_5")) if g is not None]
    g1 = max(0.0, min(min(g_hist) if g_hist else 0.0, st["max_stage1_growth"]))
    r = st["risk_free"] + st["equity_premium"]
    gt = min(st["terminal_growth"], r - 0.02)
    pv, cf = 0.0, base
    for t in range(1, 11):
        cf *= 1 + g1
        pv += cf / (1 + r) ** t
    pv += cf * (1 + gt) / (r - gt) / (1 + r) ** 10
    value = pv - m['debt'] + m['cash']
    basis = "median FCF across the cycle" if cyclical else "3y avg FCF"
    return value, f"{basis} {num(base, 0)} cr, growth {pct(g1)} for 10y then {pct(gt)}, discount {pct(r)}"


def justified_pb_value(m, st):
    """For lenders: justified P/B = (ROE - g) / (ke - g), applied to current book value."""
    roes = _vals(m["roe"], 5)
    eq = m["equity"]
    if len(roes) < 3 or not eq or eq[-1] is None or eq[-1] <= 0:
        return None, "need 3+ years of ROE"
    roe = statistics.median(roes)
    ke = st["risk_free"] + st["equity_premium"] + 0.01      # extra 1% for leverage risk
    payout = m.get("payout") or 0.0
    g = min(roe * (1 - payout), m.get("bvps_cagr_5") or roe, ke - 0.02)
    if roe <= g:
        return eq[-1], "ROE not above growth; valued at book"
    pb = (roe - g) / (ke - g)
    return pb * eq[-1], f"median ROE {pct(roe)}, g {pct(g)}, ke {pct(ke)} -> justified P/B {num(pb)}"


def margin_of_safety(value, mcap):
    if value is None or not mcap:
        return None
    return 1 - mcap / value if value > 0 else -1.0


# ------------------------------------------------------------------ philosophies

def buffett(m, rec, st, cycle_policy="legacy"):
    fin = rec["sector_type"] != "general"
    roe = _vals(m["roe"], 10)
    frac15 = sum(r >= 0.15 for r in roe) / len(roe) if len(roe) >= 4 else None
    out = [C("roe_consistency", "ROE >= 15% in most years", 20, linear(frac15, 0.3, 0.8) if frac15 is not None else None,
             f"{pct(frac15, 0)} of {len(roe)} years", "full marks if >=80% of years, zero at 30%"),
           C("no_losses", "No loss years", 5, (1.0 if m["losses"] == 0 else 0.0) if m["np_years"] else None,
             f"{m['losses']} loss year(s)", "any loss year scores zero"),
           C("eps_growth", "Long-run EPS growth", 10,
             linear(m["eps_cagr_10"] if m["eps_cagr_10"] is not None else m["eps_cagr_5"], 0.0, 0.12),
             g_text(m, "eps_cagr_10") if m["eps_cagr_10"] is not None else g_text(m, "eps_cagr_5"),
             "zero at 0%, full at 12% a year; uses up to 10 years")]
    if fin:
        roa = _vals(m["roa"], 5)
        roa_med = statistics.median(roa) if len(roa) >= 3 else None
        full = 0.012 if rec["sector_type"] == "bank" else 0.025
        value, note = justified_pb_value(m, st)
        mos = margin_of_safety(value, m["market_cap"])
        gnpa = rec.get("manual", {}).get("gnpa_pct")
        if lenders(rec):
            out += [C("roa", "Return on assets", 10, linear(roa_med, full / 3, full), pct(roa_med, 2), f"full marks at {pct(full)}"),
                    C("asset_quality", "Gross NPA (manual input)", 10, linear(gnpa, 8, 2) if gnpa is not None else None,
                      "n/a" if gnpa is None else f"{gnpa:.2f}%", "full marks at <=2%, zero at 8%")]
        else:   # insurers: ROA and NPAs don't apply; premium growth does
            out.append(C("premium_growth", "Premium income growth", 20, linear(m["sales_cagr_5"], 0.0, 0.15),
                         g_text(m, "sales_cagr_5"), "full at 15%/yr"))
        out += [C("book_growth", "Book value/share growth", 15, linear(m["bvps_cagr_5"], 0.03, 0.14), g_text(m, "bvps_cagr_5"),
                  "zero at 3%, full at 14% a year"),
                C("margin_of_safety", "Margin of safety (justified P/B)", 20, linear(mos, -0.25, 0.25), pct(mos), note)]
    else:
        value, note = dcf_value(m, st, cyclical=lynch_category(m, rec, st, cycle_policy) == "cyclical")
        mos = margin_of_safety(value, m["market_cap"])
        out += [C("low_debt", "Low debt / equity", 15, linear(_last(m["de_ratio"]), 1.5, 0.5), num(_last(m["de_ratio"])),
                  "full marks <=0.5, zero at 1.5"),
                C("owner_earnings", "Profits turn into free cash", 15, linear(m["fcf_conversion"], 0.3, 0.8),
                  num(m["fcf_conversion"]), "5y free cash flow / net profit; full at 0.8, zero at 0.3"),
                C("capital_light", "Low capital needs", 5, linear(m["capex_to_cfo"], 1.0, 0.5), num(m["capex_to_cfo"]),
                  "5y capex / operating cash; full at <=0.5, zero at 1.0"),
                C("interest_cover", "Interest cover", 5, linear(m["interest_cover"], 1.5, 5.0), num(m["interest_cover"]),
                  "full marks >=5x"),
                C("margin_of_safety", "Margin of safety (DCF)", 15, linear(mos, -0.25, 0.25), pct(mos), note)]
    out.append(C("retained_earnings", "Each rupee retained created >= Rs1 of market value", 10,
                 linear(m["retained_test"], 0.0, 1.0), num(m["retained_test"]), "over up to 5 years"))
    return out


ESSENTIALS = {
    "buffett": {"roe_consistency": 0.5, "low_debt": 0.5, "margin_of_safety": 0.5, "asset_quality": 0.5},
    "munger": {"roce_level": 0.5, "roce_floor": 0.5, "roe_level": 0.5, "roe_floor": 0.5, "asset_quality": 0.5},
    "lynch": {"peg": 1 / 3, "normalised_pe": 1 / 3, "balance_sheet": 0.5, "asset_quality": 0.5},
}
ESSENTIAL_WHY = {
    "roe_consistency": "Buffett's first test: a durable high return on equity",
    "low_debt": "Buffett avoids businesses that depend on leverage",
    "margin_of_safety": "Buffett's price test: not above a conservative estimate of value",
    "roce_level": "Munger wants an excellent business, not just a cheap one",
    "roce_floor": "the moat has to hold in bad years",
    "roe_level": "Munger wants an excellent lender, not just a cheap one",
    "roe_floor": "returns have to hold in bad years",
    "peg": "Lynch would not overpay for growth (ratio must be at least 1)",
    "normalised_pe": "cyclicals must be cheap on mid-cycle earnings",
    "balance_sheet": "Lynch insisted on a balance sheet that can't sink the company",
    "asset_quality": "a lender can't be judged without its bad loans; enter gross NPA (must be 5% or less)",
}


def P(key, label, max_penalty, score, value, rule):
    """A red flag: deducts up to `max_penalty` points (inversion: avoid stupidity), never adds."""
    c = C(key, label, max_penalty, score, value, rule)
    c["kind"] = "penalty"
    return c


def munger(m, rec, st):
    """Core (100 pts): an excellent business at a fair price. Red flags then subtract points."""
    fin = rec["sector_type"] != "general"
    man = rec.get("manual", {})
    pledge = man.get("pledge_pct")
    flags = [P("no_pledge", "Promoter pledge (manual input)", 10, linear(pledge, 20, 0) if pledge is not None else None,
               "n/a" if pledge is None else f"{pledge:.1f}% of promoter shares", "full deduction at 20% pledged"),
             P("no_dilution", "Persistent dilution", 8, linear(m["shares_cagr_5"], 0.03, 0.005), g_text(m, "shares_cagr_5"),
               "share count growth; no deduction <=0.5%/yr, full at 3%/yr")]
    eps_g = m["eps_cagr_10"] if m["eps_cagr_10"] is not None else m["eps_cagr_5"]
    eps_g_txt = g_text(m, "eps_cagr_10") if m["eps_cagr_10"] is not None else g_text(m, "eps_cagr_5")
    if fin:
        roe = _vals(m["roe"], 10)
        core = [C("roe_level", "High, durable ROE", 25, linear(statistics.median(roe) if len(roe) >= 3 else None, 0.10, 0.18),
                  (pct(statistics.median(roe)) + f" over {len(roe)}y") if len(roe) >= 3 else "n/a", "median ROE; full at 18%"),
                C("roe_floor", "ROE never collapsed", 15, linear(min(_vals(m["roe"], 5)) if len(_vals(m["roe"], 5)) >= 3 else None, 0.0, 0.12),
                  pct(min(_vals(m["roe"], 5))) if _vals(m["roe"], 5) else "n/a", "worst year in 5; full at 12%"),
                C("growth", "Book value growth", 15, linear(m["bvps_cagr_5"], 0.03, 0.15), g_text(m, "bvps_cagr_5"), "full at 15%/yr"),
                C("eps_growth", "Earnings growth", 10, linear(eps_g, 0.03, 0.15), eps_g_txt, "zero at 3%, full at 15%/yr"),
                C("asset_quality", "Gross NPA (manual input)", 15,
                  linear(man.get("gnpa_pct"), 8, 2) if man.get("gnpa_pct") is not None else None,
                  "n/a" if man.get("gnpa_pct") is None else f"{man['gnpa_pct']:.2f}%", "full at <=2%")
                if lenders(rec) else
                C("premium_growth", "Premium income growth", 15, linear(m["sales_cagr_5"], 0.0, 0.15),
                  g_text(m, "sales_cagr_5"), "full at 15%/yr"),
                C("fair_price", "Fair price (P/B vs ROE)", 20,
                  linear((statistics.median(roe) / m["pb"]) if len(roe) >= 3 and m["pb"] else None, 0.04, 0.10),
                  num(m["pb"]), "ROE / (P/B) as an earnings yield on price; full at 10%")]
        return core + flags
    roce = _vals(m["roce"], 10)
    roce5 = _vals(m["roce"], 5)
    margins = _vals(m["ebit_margin"], 10)
    cv = (statistics.pstdev(margins) / statistics.fmean(margins)) if len(margins) >= 4 and statistics.fmean(margins) > 0 else None
    fcf_yield = (m["fcf_avg_3"] / m["market_cap"]) if m["fcf_avg_3"] is not None and m["market_cap"] else None
    recv_gap = (m["recv_cagr_5"] - m["sales_cagr_5"]) if m["recv_cagr_5"] is not None and m["sales_cagr_5"] is not None else None
    sales_g = m["sales_cagr_10"] if m["sales_cagr_10"] is not None else m["sales_cagr_5"]
    sales_g_txt = g_text(m, "sales_cagr_10") if m["sales_cagr_10"] is not None else g_text(m, "sales_cagr_5")
    core = [C("roce_level", "Exceptional return on capital", 25, linear(statistics.median(roce) if len(roce) >= 4 else None, 0.10, 0.25),
              (pct(statistics.median(roce)) + f" over {len(roce)}y") if len(roce) >= 4 else "n/a", "median ROCE; zero at 10%, full at 25%"),
            C("roce_floor", "Moat held in bad years", 10, linear(min(roce5) if len(roce5) >= 3 else None, 0.05, 0.15),
              pct(min(roce5)) if roce5 else "n/a", "worst ROCE in 5y; full at 15%"),
            C("margin_stability", "Stable operating margins", 10, linear(cv, 0.5, 0.15), num(cv),
              "coefficient of variation of EBIT margin; full <=0.15"),
            C("growth", "Revenue growth (runway)", 10, linear(sales_g, 0.03, 0.12), sales_g_txt, "zero at 3%, full at 12%/yr"),
            C("eps_growth", "Earnings compounding", 10, linear(eps_g, 0.03, 0.15), eps_g_txt, "zero at 3%, full at 15%/yr"),
            C("fair_price", "Fair price (free-cash-flow yield vs G-Sec)", 20,
              linear(fcf_yield / st["risk_free"] if fcf_yield is not None else None, 0.3, 1.0), pct(fcf_yield),
              f"full when FCF yield >= G-Sec ({pct(st['risk_free'])}), zero at 30% of it"),
            C("low_debt", "Little debt", 15, linear(_last(m["de_ratio"]), 1.0, 0.3), num(_last(m["de_ratio"])), "full <=0.3, zero at 1.0")]
    flags += [P("earnings_quality", "Earnings not backed by cash", 8, linear(m["cash_conversion"], 0.6, 0.9), num(m["cash_conversion"]),
                "5y operating cash / net profit; deduction below 0.9, full at 0.6"),
              P("receivables", "Receivables outrunning sales", 6, linear(recv_gap, 0.10, 0.0),
                pct(recv_gap) + " points" if recv_gap is not None else "n/a", "5y receivable CAGR minus sales CAGR; full deduction at +10 points"),
              P("other_income", "Profit propped by other income", 6, linear(m["other_income_share"], 0.35, 0.15),
                pct(m["other_income_share"]), "3y other income / PBT; deduction above 15%, full at 35%"),
              P("tax", "Unusually low tax", 4, linear(m["tax_rate_avg"], 0.10, 0.20), pct(m["tax_rate_avg"]),
                "5y tax / PBT; deduction below 20%, full below 10%")]
    return core + flags


# NSE industries whose earnings swing with the economic or commodity cycle (Lynch: autos, steel, chemicals, oil...).
CYCLICAL_INDUSTRIES = {"Oil Gas & Consumable Fuels", "Metals & Mining", "Chemicals", "Construction Materials",
                       "Realty", "Automobile and Auto Components", "Textiles"}


# Screener's finer industry names (used for companies outside the Nifty list).
CYCLICAL_WORDS = re.compile(r"refiner|oil|gas|petro|steel|iron|metal|mining|alumin|copper|zinc|cement|chemical|fertili"
                            r"|automobile|2/3 wheel|passenger car|commercial vehicle|tractor|tyre|auto comp|realty|real estate"
                            r"|residential|textile|sugar|paper|coal|shipping", re.I)


def is_cyclical_industry(rec):
    ind = rec.get("industry") or ""
    return ind in CYCLICAL_INDUSTRIES or bool(CYCLICAL_WORDS.search(ind))


def lynch_category(m, rec, st, policy="legacy"):
    if policy not in ("legacy", "sector-first-v1"):
        raise ValueError("Unknown cyclical classification policy")
    if policy == "sector-first-v1" and rec["sector_type"] == "general" and is_cyclical_industry(rec):
        return "cyclical"
    g = m["eps_cagr_5"]
    eps = _vals(m["eps"], 6)
    if m["n_years"] < 7 and is_cyclical_industry(rec):
        return "cyclical"   # too little history to see a cycle, so go by industry
    if len(eps) >= 4 and min(eps[-4:-1]) <= 0 < eps[-1]:
        return "turnaround"   # a loss in the 3 years before the latest, now profitable
    big_drops = sum(1 for a, b in zip(eps, eps[1:]) if a > 0 and b < 0.8 * a)
    if big_drops >= 2:
        return "cyclical"   # EPS fell >20% in two or more of the last five years
    nc = ((m.get("cash") or 0) + (m.get("investments") or 0)) / m["market_cap"] if m["market_cap"] else 0
    if rec["sector_type"] == "general" and nc > 0.5:
        return "asset play"
    if g is None:
        return "unclassified"
    if g >= 0.20:
        return "fast grower"
    if g >= 0.10:
        return "stalwart"
    return "slow grower"


def cycle_assessment(m, rec, st):
    """Expose the same diagnostic even when legacy classification skips it.
    A median of available annual EPS is a proxy, not proof of a complete cycle.
    Negative earnings remain in the sample; missing years are never zero-filled.
    """
    pairs = list(zip(m['years'], m['eps']))[-10:]
    usable = [(year, eps) for year, eps in pairs if eps is not None and math.isfinite(eps)]
    median = statistics.median(eps for _, eps in usable) if len(usable) >= 3 else None
    npe = m['price']/median if median is not None and median > 0 and m.get('price') and m['price'] > 0 else None
    score = 0.0 if median is not None and median <= 0 else linear(npe,25,10)
    exposed = rec['sector_type'] == 'general' and is_cyclical_industry(rec)
    category = lynch_category(m,rec,st)
    return {'industry_flag':exposed, 'legacy_category':category,
            'comparison_category':lynch_category(m,rec,st,'sector-first-v1'),
            'legacy_omits_cycle_test':exposed and category != 'cyclical',
            'median_eps':median, 'normalised_pe':npe, 'criterion_score':score,
            'observations':len(usable), 'periods':[year for year,_ in usable],
            'missing_periods':[year for year,eps in pairs if eps is None],
            'short_history':len(usable)<7, 'policy_version':'sector-first-v1',
            'note':'Same rule for each flagged industry: price / median of up to 10 available annual EPS values, including losses. '
                   'Full credit at 10x or below; zero at 25x or above; essential test requires 20x or below. '
                   'History lengths and cycle phases can differ. A six-year or even ten-year median does not establish a full cycle. '
                   'Broad industry tags require business-specific review; the comparison does not change live signals.'}


def lynch(m, rec, st, cycle_policy="legacy"):
    man = rec.get("manual", {})
    category = lynch_category(m, rec, st, cycle_policy)
    g = m["eps_cagr_5"]
    dy = m["dividend_yield"] or 0.0
    ratio = ((g * 100 + dy * 100) / m["pe"]) if g is not None and m["pe"] else None
    sweet = None if g is None else 1.0 if 0.15 <= g <= 0.25 else 0.5 if 0.10 <= g < 0.15 or 0.25 < g <= 0.35 else 0.0
    eps5 = _vals(m["eps"], 6)
    ups = sum(1 for a, b in zip(eps5, eps5[1:]) if b > a) / (len(eps5) - 1) if len(eps5) >= 4 else None
    size = m.get("size") or size_bucket(m["market_cap"], st)
    if category == "cyclical":
        # Lynch: with cyclicals a low P/E often marks peak earnings. Value them on mid-cycle (median) earnings instead.
        cycle = cycle_assessment(m, rec, st)
        npe, npe_score = cycle['normalised_pe'], cycle['criterion_score']
        out = [C("normalised_pe", "P/E on mid-cycle earnings (cyclical)", 25, npe_score, num(npe, 1),
                 f"price / median EPS of {cycle['observations']} years (current P/E {num(m['pe'], 1)} may reflect a peak); full <=10, zero >=25"),
               C("survive_downturn", "Can survive a downturn", 15, linear(m["interest_cover"], 2.0, 6.0), num(m["interest_cover"]),
                 "interest cover; full >=6x")]
    else:
        out = [C("peg", "(Growth + yield) / P/E", 25, linear(ratio, 0.5, 2.0), num(ratio),
                 f"Lynch: 2+ excellent, 1 okay, <1 poor. P/E {num(m['pe'], 1)} on {m.get('pe_basis', 'latest year')}"),
               C("growth_sweet_spot", "EPS growth 15-25% (sustainable)", 15, sweet, g_text(m, "eps_cagr_5"),
                 "full in 15-25%; half 10-15% or 25-35%")]
    out += [
           C("consistency", "EPS rose in most years", 10, linear(ups, 0.4, 0.8), pct(ups, 0), "share of the last 5 years EPS grew"),
           C("promoter", "Promoter skin in the game (manual)", 10,
             linear(man.get("promoter_pct"), 25, 50) if man.get("promoter_pct") is not None else None,
             "n/a" if man.get("promoter_pct") is None else f"{man['promoter_pct']:.1f}%", "full at >=50%"),
           C("under_owned", "Not yet crowded by institutions (manual)", 5,
             linear(man.get("institutional_pct"), 60, 30) if man.get("institutional_pct") is not None else None,
             "n/a" if man.get("institutional_pct") is None else f"{man['institutional_pct']:.1f}%", "FII+DII; full <=30%"),
           C("room_to_grow", "Size leaves room to grow", 5, {"small": 1.0, "mid": 0.6, "large": 0.3}.get(size), size or "n/a",
             "small 100%, mid 60%, large 30%")]
    if rec["sector_type"] == "general":
        net_cash = (m["cash"]-m["debt"]) / m["market_cap"] if m["market_cap"] and m.get("cash") is not None and m.get("debt") is not None else None
        inv_gap = (m["inv_cagr_5"] - m["sales_cagr_5"]) if m["inv_cagr_5"] is not None and m["sales_cagr_5"] is not None else None
        out += [C("balance_sheet", "Strong balance sheet", 10, linear(_last(m["de_ratio"]), 1.0, 0.35), num(_last(m["de_ratio"])),
                  "debt/equity; full <=0.35"),
                C("inventory", "Inventory not piling up", 10, linear(inv_gap, 0.10, 0.0), pct(inv_gap),
                  "5y inventory CAGR minus sales CAGR"),
                C("net_cash", "Net cash cushion", 10, linear(net_cash, -0.2, 0.1), pct(net_cash), "(cash - debt) / market cap")]
    else:
        out.append(C("book_growth", "Book value growth", 20, linear(m["bvps_cagr_5"], 0.0, 0.15), g_text(m, "bvps_cagr_5"), "full at 15%/yr"))
        out.append(C("asset_quality", "Gross NPA (manual input)", 10,
                     linear(man.get("gnpa_pct"), 8, 2) if man.get("gnpa_pct") is not None else None,
                     "n/a" if man.get("gnpa_pct") is None else f"{man['gnpa_pct']:.2f}%", "full at <=2%")
                   if lenders(rec) else
                   C("premium_growth", "Premium income growth", 10, linear(m["sales_cagr_5"], 0.0, 0.15),
                     g_text(m, "sales_cagr_5"), "full at 15%/yr"))
    return out


def _last(lst):
    return lst[-1] if lst else None


def summarise(criteria, st, philosophy=None):
    ess = ESSENTIALS.get(philosophy, {})
    for c in criteria:
        if c["key"] in ess and c.get("kind") != "penalty":
            c["essential_min"] = ess[c["key"]]
            c["why_essential"] = ESSENTIAL_WHY.get(c["key"], "")
    failed = [c["label"] for c in criteria if "essential_min" in c and c["score"] is not None and c["score"] < c["essential_min"]]
    unknown = [c["label"] for c in criteria if "essential_min" in c and c["score"] is None]
    core = [c for c in criteria if c.get("kind") != "penalty"]
    flags = [c for c in criteria if c.get("kind") == "penalty"]
    total = sum(c["weight"] for c in core)
    have = [c for c in core if c["score"] is not None]
    w = sum(c["weight"] for c in have)
    base = 100 * sum(c["weight"] * c["score"] for c in have) / w if w else None
    deduction = sum((1 - c["score"]) * c["weight"] for c in flags if c["score"] is not None)
    score = max(0.0, base - deduction) if base is not None else None
    coverage = w / total if total else 0
    unknown_penalty = sum(c['weight'] for c in flags if c['score'] is None)
    conservative = max(0.0, 100*sum(c['weight']*c['score'] for c in have)/total-deduction-unknown_penalty) if total else None
    for c in criteria:
        c['state'] = ('unknown' if c['score'] is None else 'passed' if c['score'] >= c.get('essential_min', 1.0) else 'failed')
    return {"score": round(score, 1) if score is not None else None,
            'conservative_score':round(conservative,1) if conservative is not None else None,
            'unknown_red_flags':[c['label'] for c in flags if c['score'] is None],
            'unassessed_penalty_points':unknown_penalty,
            'score_note':'Legacy score rescales available core criteria; conservative score keeps all weights and reserves unknown red-flag deductions.',
            "base_score": round(base, 1) if base is not None else None,
            "deductions": round(deduction, 1), "coverage": round(coverage, 2),
            "reliable": coverage >= st["min_coverage"],
            "passes_mark": score is not None and score >= st["pass_score"],
            "failed_essentials": failed, "unknown_essentials": unknown,
            "matches": (score is not None and coverage >= st["min_coverage"] and score >= st["pass_score"]
                        and not failed and not unknown),
            "criteria": criteria}


def price_headroom(rec, st=None, size_rank=None, entry=False):
    """How far the share price can move before the Buy/Sell picture changes, all else equal.
    If the stock matches a philosophy now: the rise at which it would match none (the Sell point).
    If it matches none: the fall at which it would start matching (the Buy point), or None when no
    price within -80% would do it (the problem is business quality, not price).
    Valuation tests (margin of safety, P/E, PEG, FCF yield, P/B) move with price; quality tests don't."""
    meta = rec.get("meta") or {}
    if not meta.get("market_cap") and not meta.get("price"):
        return None
    st = st or settings()
    if entry and st.get('entry_margin_of_safety',-1) >= 0:
        # A separate valuation ceiling is displayed elsewhere; do not label the legacy quality boundary a Buy trigger.
        return None
    price = meta.get("price")
    cache = {}

    def matched(f):
        if f not in cache:
            r = {**rec, "meta": {**meta, "price": (price or 0) * f or None,
                                 "market_cap": (meta.get("market_cap") or 0) * f or None}}
            cache[f] = set(score_company(r, st, size_rank)["matches"])
        return cache[f]

    def boundary(test, lo, hi):
        """Invariant: test(lo) true, test(hi) false. Bisect to ~0.01%."""
        for _ in range(18):
            mid = (lo + hi) / 2
            if test(mid):
                lo = mid
            else:
                hi = mid
        return lo, hi

    now = matched(1.0)
    each = {}
    for k in ("buffett", "munger", "lynch"):
        if k in now:           # how far can price rise before this philosophy stops matching?
            if k in matched(10.0):
                each[k] = {"direction": "up", "pct": None}
            else:
                _, hi = boundary(lambda f: k in matched(f), 1.0, 10.0)
                each[k] = {"direction": "up", "pct": hi - 1}
        else:                  # how far must it fall before this philosophy starts matching?
            if k not in matched(0.2):
                each[k] = {"direction": "down", "pct": None}
            else:
                lo, _ = boundary(lambda f: k in matched(f), 0.2, 1.0)
                each[k] = {"direction": "down", "pct": lo - 1}
    if now:
        ups = [e["pct"] for k, e in each.items() if k in now]
        if any(p is None for p in ups):
            insensitive = [k.title() for k, e in each.items() if k in now and e["pct"] is None]
            out = {"direction": "up", "pct": None,
                   "note": f"{' and '.join(insensitive)} {'still matches' if len(insensitive) == 1 else 'still match'} even at 10x "
                           "today's price, and there's no value estimate for price discipline to use, so price alone won't turn this into a Sell"}
        else:
            f = 1 + max(ups)
            out = {"direction": "up", "pct": f - 1, "price": price * f if price else None,
                   "note": "rise at which it stops matching every philosophy (the Sell point)"}
    else:
        downs = [e["pct"] for e in each.values() if e["pct"] is not None]
        if not downs:
            out = {"direction": "down", "pct": None,
                   "note": "no price within -80% makes it match: the issue is business quality, not price"}
        else:
            f = 1 + max(downs)
            out = {"direction": "down", "pct": f - 1, "price": price * f if price else None,
                   "note": "fall at which it starts matching a philosophy (the Buy point)"}
    # Price discipline: the signal also flips where price crosses `limit` x the value estimate. The estimate
    # doesn't depend on price, so that point is simply limit / today's multiple.
    st = st or settings()
    lim = st.get("price_discipline") or 0
    mult = valuation(fundamentals_metrics(rec, size_rank, st), rec, st)["multiple"]
    if lim and mult:
        fd = lim / mult
        disc = f"price passes {lim:g}x the estimated value (price discipline)"
        if now and fd < 1:
            out = {"direction": "down", "pct": fd - 1, "price": price * fd if price else None,
                   "note": f"fall needed to get back under {lim:g}x the estimated value; until then a held stock shows "
                           "Sell and a shortlisted one can't show Buy"}
        elif now and (out["pct"] is None or fd - 1 < out["pct"]):
            out = {"direction": "up", "pct": fd - 1, "price": price * fd if price else None,
                   "note": f"rise at which the {disc} (the Sell point)"}
        elif not now and out["pct"] is not None and fd < 1 + out["pct"]:
            out = {**out, "pct": fd - 1, "price": price * fd if price else None,
                   "note": f"fall at which it matches a philosophy and is under {lim:g}x the estimated value (the Buy point)"}
    out["each"] = each
    out["multiple"] = mult
    return out


def fundamentals_metrics(rec, size_rank, st):
    from .fundamentals import metrics
    m = metrics(rec)
    m["size"] = size_bucket(m["market_cap"], st, size_rank)
    return m


def score_company(rec, st=None, size_rank=None, headroom=False, model="legacy"):
    if model not in ("legacy", "operating-v1"):
        raise ValueError("Unknown scoring model")
    from .fundamentals import metrics
    st = st or settings()
    m = metrics(rec, operating=model == "operating-v1")
    cycle_policy = "sector-first-v1" if model == "operating-v1" else "legacy"
    m["size"] = size_bucket(m["market_cap"], st, size_rank)
    result = {"symbol": rec["symbol"], "name": rec.get("name"), "basis": rec["basis"], "sector_type": rec["sector_type"],
              "years": f"{m['years'][0]} to {m['years'][-1]}", "n_years": m["n_years"],
              "market_cap_cr": m["market_cap"], "size": m["size"], "size_basis": "rank" if size_rank else "cut-offs",
              "pe": m["pe"], "pe_basis": m.get("pe_basis"), "pb": m["pb"], "ttm": m.get("ttm"),
              "lynch_category": lynch_category(m, rec, st, cycle_policy),
              "cycle_assessment": cycle_assessment(m, rec, st),
              "buffett": summarise(buffett(m, rec, st, cycle_policy), st, "buffett"),
              "munger": summarise(munger(m, rec, st), st, "munger"),
              "lynch": summarise(lynch(m, rec, st, cycle_policy), st, "lynch")}
    # Latest-year and 5-year-median returns on capital. For banks, NBFCs and insurers borrowed money is the raw
    # material, not financing, so ROCE/ROIC aren't meaningful; ROE and ROA are their equivalents.
    med5 = lambda k: statistics.median(_vals(m[k], 5)) if len(_vals(m[k], 5)) >= 3 else None
    result["returns"] = {"applicable": rec["sector_type"] == "general", "year": m["years"][-1],
                         **{k: _last(m[k]) for k in ("roce", "roic", "roe")},
                         **{k + "_5y": med5(k) for k in ("roce", "roic", "roe")}}
    if model == 'operating-v1':
        for philosophy in ('buffett','munger','lynch'):
            summary = result[philosophy]
            summary['legacy_normalized_score'] = summary['score']
            summary['score'] = summary['conservative_score']
            summary['passes_mark'] = summary['score'] is not None and summary['score'] >= st['pass_score']
            summary['matches'] = summary['passes_mark'] and summary['reliable'] and not summary['failed_essentials'] and not summary['unknown_essentials']
    result['metric_notes'] = m['operating_returns']['note']
    result['capex_sources'] = list(zip(rec['years'], m['capex_sources']))
    result['share_evidence'] = m['share_evidence']
    result['ttm_unavailable_reason'] = m['ttm_unavailable_reason']
    result["valuation"] = valuation(m, rec, st, cycle_policy)
    issues = []
    today = config.now_ist().date()
    meta = rec.get("meta") or {}
    if rec['years'] != sorted(set(rec['years'])):
        issues.append('annual periods are duplicated or out of order')
    qdates = (rec.get('quarters') or {}).get('dates', [])
    if qdates and (qdates != sorted(set(qdates)) or qdates[-1] > today.isoformat()):
        issues.append('quarterly periods are duplicated, out of order or future-dated')
    for label, value, max_days in (("price", meta.get("price_date") or meta.get("market_cap_date"), 7),
                                   ("annual financials", rec["years"][-1], 450)):
        try:
            age = (today - dt.date.fromisoformat(str(value)[:10])).days
            if age < 0 or age > max_days:
                issues.append(f"{label} dated {value} is outside the freshness limit")
        except (ValueError, TypeError):
            issues.append(f"{label} date missing or invalid")
    if not any(result[k]["reliable"] and not result[k]["unknown_essentials"] for k in ("buffett", "munger", "lynch")):
        issues.append("insufficient essential financial data")
    result["data_quality"] = {"issues": issues, "checked_at": today.isoformat()}
    if rec['sector_type'] == 'general' and (m['debt'] is None or m['cash'] is None or m['equity'][-1] is None or m['equity'][-1] <= 0):
        issues.append('latest debt, cash or positive equity unavailable')
    if any(not isinstance(v, (int,float)) or isinstance(v,bool) or not math.isfinite(v) or v <= 0 for v in (m['price'],m['market_cap'])):
        issues.append('current price or market cap missing/invalid')
    if qdates and len(qdates) >= 4 and m['ttm_unavailable_reason']:
        issues.append('unsuitable quarterly earnings: '+m['ttm_unavailable_reason'])
    result['model_status'] = 'production legacy policy' if model == 'legacy' else 'experimental comparison only'
    result["algorithm_version"] = "2.2-safety" if model == 'legacy' else 'operating-cycle-score-v1'
    result['inapplicable_metrics'] = ['industrial ROIC','industrial ROCE','industrial FCF'] if rec['sector_type'] != 'general' else []
    financial_stale = any('annual financials' in issue for issue in issues)
    for philosophy in ('buffett','munger','lynch'):
        for criterion in result[philosophy]['criteria']:
            criterion['freshness'] = 'stale' if financial_stale else 'within annual limit'

    matched = [k for k in ("buffett", "munger", "lynch") if result[k]["matches"]]
    result["matches"] = matched
    result["all_three"] = len(matched) == 3
    warnings = []
    if m["n_years"] < 6:
        warnings.append(f"only {m['n_years']} years of annual data: growth rates cover {m['n_years'] - 1} years, "
                        "and long-run consistency tests are weak. Re-import the 10-year export when you have it.")
    if rec["basis"] == "standalone":
        warnings.append("standalone statements: check the company has no material subsidiaries")
    if m["market_cap"] is None:
        warnings.append("no market cap in the file; valuation criteria could not run")
    warnings += list(rec.get("period_notes") or [])
    if not any(v is not None for v in (rec["series"].get("price") or [])):
        warnings.append("no year-end share prices yet, so the retained-earnings test can't run; use 'Fill prices from Yahoo'")
    if not (rec.get("quarters") or {}).get("dates"):
        warnings.append(f"no quarterly data: P/E uses the last annual report ({m['years'][-1]})")
    if (rec.get("meta") or {}).get("market_cap_date"):
        warnings.append(f"market cap as of {rec['meta']['market_cap_date']}")
    result["capex_source"] = m.get("capex_source")
    result["provisional"] = m["n_years"] < 6
    if result["lynch_category"] == "cyclical" and m["n_years"] < 7 and is_cyclical_industry(rec):
        warnings.append(f"treated as cyclical because of its industry ({rec['industry']}); the history is too short to show a cycle, "
                        "so mid-cycle earnings are estimated from only a few years")
    result["industry"] = rec.get("industry")
    if result['cycle_assessment']['legacy_omits_cycle_test']:
        warnings.append('Industry suggests cyclical exposure, but legacy classification omits the mid-cycle test. See the sector-first experimental comparison.')
    result["warnings"] = warnings
    if headroom:
        result["headroom"] = None if issues or result['provisional'] else price_headroom(rec, st, size_rank)
    return result
