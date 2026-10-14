"""Explainable headline triage. Never treats a headline as a verified financial fact."""
import re


def assess(head, record=None):
    title = head['title']
    text = title.lower()
    source = 'exchange disclosure' if head.get('source') == 'NSE corporate announcement' else 'secondary report'
    urgent = re.search(r'\b(default(?:s|ed)?|insolvency|fraud|auditor.{0,25}resign\w*|credit.{0,15}downgrad\w*)\b', text)
    qualified = re.search(r'\b(no|not|denies|denied|denial|avoids|averted|alleged|allegation|rumou?r)\b', text)
    qualified = qualified or re.search(r'\b(default settings|fraud (?:prevention|awareness|protection)|anti[- ]fraud)\b', text)
    routine = re.search(r'\b(trading window|compliance certificate|loss of share certificate|duplicate share certificate|board meeting intimation|newspaper publication)\b', text)
    cats = head.get('categories', [])
    if urgent:
        label = 'investment review' if qualified else 'urgent review'
        reason = 'Credit or governance language detected; negation, allegations and context require verification.'
    elif routine:
        label, reason = 'routine filing', 'Administrative filing detected; attached documents may contain additional information.'
    elif any(c in cats for c in ('results', 'legal/regulatory', 'merger/acquisition', 'governance/management', 'deal/order')):
        label, reason = 'investment review', 'Potential change to earnings, financing or governance; verify the original document.'
    else:
        label, reason = 'new information', 'Financial impact cannot be established from the headline.'
    result = {'label':label, 'reason':reason, 'source_type':source, 'verified_impact':False, 'context':None}
    # Only contextualize one explicitly denominated order/contract amount; never guess currency or select
    # one number from a headline containing several values. A multi-year order is not annual revenue.
    amounts = re.findall(r'(?:₹|\brs\.?|\binr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\b|lakhs?|million|billion)', text)
    if len(amounts) == 1 and re.search(r'\b(order|orders|contract|contracts)\b', text) and record:
        amount, unit = amounts[0]
        factors = {'million':0.1, 'billion':100, 'lakh':0.01, 'lakhs':0.01}
        amount_cr = float(amount.replace(',', '')) * factors.get(unit, 1)
        years = record.get('years') or []
        sales = record.get('series', {}).get('sales') or []
        from . import config
        import datetime as dt
        try:
            fresh = 0 <= (config.now_ist().date()-dt.date.fromisoformat(years[-1])).days <= 450
        except (ValueError, IndexError):
            fresh = False
        if fresh and sales and sales[-1] is not None and sales[-1] > 0:
            result['context'] = (f'Headline amount ₹{amount_cr:,.1f} cr is {amount_cr/sales[-1]:.1%} of reported '
                                 f'annual revenue ({years[-1]}). Order duration, ownership share, execution and margins '
                                 'are unverified; this is not an earnings forecast.')
    return result
