"""Evidence-preserving financial helpers. Monetary units and shares are crore.

Operating ROIC excludes reported non-operating income. Cash exclusion is an explicit
approximation: statements do not usually identify surplus versus operating cash.
No statutory tax rate or artificial invested-capital floor is supplied for missing data.
"""
import calendar
import datetime as dt
import math

from . import config


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quarterly_sequence(quarters, today=None):
    """Four complete, strictly ordered consecutive three-month reporting periods.
    Month-first labels from Excel exports are supported, but mixed first/end labels
    and irregular period lengths are rejected. No missing quarter is estimated.
    """
    today = today or config.now_ist().date()
    dates = quarters.get('dates') or []
    if len(dates) < 4:
        return False, 'fewer than four quarters'
    try:
        parsed = [dt.date.fromisoformat(d) for d in dates]
    except (TypeError, ValueError):
        return False, 'invalid quarterly dates'
    if parsed != sorted(set(parsed)) or parsed[-1] > today:
        return False, 'duplicate, unordered or future quarterly dates'
    last = parsed[-4:]
    if not (all(d.day == 1 for d in last) or all(d.day == calendar.monthrange(d.year,d.month)[1] for d in last)):
        return False, 'quarter end dates are inconsistent'
    months = [d.year*12+d.month for d in last]
    if any(b-a != 3 for a,b in zip(months,months[1:])):
        return False, 'quarters are not consecutive'
    lengths = quarters.get('period_months')
    if lengths is not None and (len(lengths) != len(dates) or any(x != 3 for x in lengths[-4:])):
        return False, 'non-quarterly reporting periods'
    profits = quarters.get('net_profit') or []
    if len(profits) != len(dates) or not all(finite(v) for v in profits[-4:]):
        return False, 'missing or invalid quarterly profit'
    # First-of-month labels describe that month's end, not information available on day 1.
    if dt.date(last[-1].year,last[-1].month,calendar.monthrange(last[-1].year,last[-1].month)[1]) > today:
        return False, 'quarter has not completed'
    return True, None


def share_evidence(record):
    """Keep weighted-average/adjusted EPS denominators separate from outstanding shares.
    Capital/face value recovery is per period only; no current face value backfill.
    """
    s = record['series']; n = len(record['years'])
    at = lambda key,i: (s.get(key) or [])[i] if i < len(s.get(key) or []) else None
    historical, sources, outstanding = [], [], []
    for i in range(n):
        count, cap, face = at('shares_count',i), at('share_capital',i), at('face_value_row',i)
        reported = count/1e7 if finite(count) and count > 0 else None
        recovered = cap/face if finite(cap) and finite(face) and cap > 0 and face > 0 else None
        if reported and recovered and abs(reported/recovered-1) > .02:
            outstanding.append(None)
        else:
            outstanding.append(reported if reported is not None else recovered)
        adjusted = at('shares_cr',i)
        if finite(adjusted) and adjusted > 0:
            historical.append(adjusted)
            sources.append((record.get('share_provenance') or {}).get('historical', 'reported adjusted shares'))
        else:
            historical.append(None)
            sources.append('unavailable: period outstanding shares are not split-adjusted EPS denominators')
    return {'adjusted_shares_cr':historical, 'historical_sources':sources,
            'outstanding_shares_cr':outstanding,
            'note':'Profit/EPS implies a weighted-average share count, not necessarily current shares outstanding. '
                   'Period capital/face value is not silently backfilled across splits, bonuses or dilution.'}


def operating_returns(record):
    s, years = record['series'], record['years']
    at = lambda key,i: (s.get(key) or [])[i] if 0 <= i < len(s.get(key) or []) else None
    ebit, ce, ic, rates, notes = [], [], [], [], []
    for i in range(len(years)):
        op = at('operating_ebit',i)
        pbt, interest, other = (at(k,i) for k in ('pbt','interest','other_income'))
        if not finite(op):
            op = pbt+interest-other if all(finite(v) for v in (pbt,interest,other)) else None
        ebit.append(op)
        capital, reserves, debt, cash, tax = (at(k,i) for k in ('share_capital','reserves','borrowings','cash','tax'))
        employed = capital+reserves+debt if all(finite(v) for v in (capital,reserves,debt)) else None
        ce.append(employed)
        ic.append(employed-cash if finite(employed) and finite(cash) else None)
        rate = tax/pbt if finite(tax) and finite(pbt) and pbt > 0 else None
        rates.append(rate if rate is not None and 0 <= rate <= .4 else None)
    def average(values,i):
        if i == 0 or not all(finite(values[j]) and values[j] > 0 for j in (i-1,i)):
            return None
        try:
            days = (dt.date.fromisoformat(years[i])-dt.date.fromisoformat(years[i-1])).days
        except ValueError:
            return None
        if not 350 <= days <= 380:
            return None
        value = (values[i-1]+values[i])/2
        # Numerical singularity guard; never manufacture a minimum capital base.
        scale = max(abs(ce[i] or 0),abs(ce[i-1] or 0),1)
        return value if value > 1e-6*scale else None
    roce, roic = [], []
    financial = record.get('sector_type') in ('bank','nbfc','insurance')
    for i in range(len(years)):
        c, invested = average(ce,i), average(ic,i)
        roce.append(ebit[i]/c if not financial and finite(ebit[i]) and c else None)
        roic.append(ebit[i]*(1-rates[i])/invested if not financial and finite(ebit[i]) and rates[i] is not None and invested else None)
    return {'ebit':ebit,'roce':roce,'roic':roic,'tax_rates':rates, 'version':'operating-returns-v1',
            'note':'EBIT = PBT + interest − separately reported other income (or explicit operating EBIT). '
                   'ROIC uses observed effective tax only within 0–40%, averaged consecutive positive capital net of cash; '
                   'near-zero/negative capital and missing tax yield unknown. All-cash exclusion and other-income classification '
                   'need source review; non-operating investments are not identified. Inapplicable to financial institutions.'}


def current_shares(record):
    """Return the latest supported denominator and its provenance, never an older year's fallback."""
    meta = record.get('meta') or {}
    if finite(meta.get('shares')) and meta['shares'] > 0:
        source = (record.get('share_provenance') or {}).get('current','reported current share count')
        return meta['shares']/1e7, source
    evidence = share_evidence(record)
    outstanding = evidence['outstanding_shares_cr'][-1] if record['years'] else None
    if outstanding is not None:
        return outstanding, 'latest period outstanding shares from reported count or matched capital/face value'
    shares = evidence['adjusted_shares_cr'][-1] if record['years'] else None
    return shares, evidence['historical_sources'][-1] if record['years'] else 'unavailable'
