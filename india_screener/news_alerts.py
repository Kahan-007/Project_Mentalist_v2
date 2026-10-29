"""Decision-focused news policy and evidence. Headlines never alter financial inputs."""
import copy
import datetime as dt
import hashlib
import json
import math

from . import config, materiality

MODES = ('actionable', 'signals_only', 'all')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS signal_evidence(
 symbol TEXT PRIMARY KEY, snapshot TEXT NOT NULL DEFAULT '{}',
 candidate TEXT, confirmations INTEGER NOT NULL DEFAULT 0, last_sample TEXT
);
CREATE TABLE IF NOT EXISTS signal_news(
 recommendation_id INTEGER NOT NULL REFERENCES recommendation_log(id),
 news_id INTEGER NOT NULL REFERENCES news(id),
 PRIMARY KEY(recommendation_id, news_id)
);
'''


def settings(cfg=None):
    return {**config.DEFAULTS['news_alerts'], **(cfg or config.load_config()).get('news_alerts', {})}


def headline_push(item, triage, cfg):
    mode = settings(cfg)['mode']
    if mode == 'all':
        return True, 'All fresh headlines enabled.'
    if mode == 'actionable' and item.get('held') and triage['label'] == 'urgent review':
        return True, 'Held-stock risk exception; verify the source before acting.'
    return False, ('Routine filing stored without a push.' if triage['label'] == 'routine filing'
                   else 'Stored for a later confirmed signal change; no headline-only push.')


def signal_push(prior, current, cfg):
    # A withdrawn Buy/Sell matters too: an earlier recommendation must not remain actionable silently.
    return settings(cfg)['mode'] == 'all' or prior in ('buy', 'sell') or current in ('buy', 'sell')


def apply_pending_filter(cfg):
    """Apply a tightened filter to unsent phone alerts too. Commands and local logs are retained."""
    if settings(cfg)['mode'] == 'all':
        return 0
    from . import db as dbm, portfolio
    held = set(portfolio.holdings())
    cancelled = 0
    with dbm.connect() as db:
        rows = db.execute("SELECT DISTINCT a.id,a.event_key FROM alert_outbox a JOIN alert_delivery d ON d.alert_id=a.id "
                          "WHERE d.status='pending' AND d.channel!='log' AND d.lease_until=0").fetchall()
        for row in rows:
            kind, _, identity = row['event_key'].partition(':')
            push = True
            if kind == 'event':
                push = False
            elif kind == 'headline':
                audit = db.execute('SELECT h.priority,n.symbol FROM headline_audit h JOIN news n ON n.id=h.news_id '
                                   'WHERE h.alert_id=?', (row['id'],)).fetchone()
                push = bool(audit and settings(cfg)['mode'] == 'actionable' and
                            audit['priority'] == 'urgent review' and audit['symbol'] in held)
            elif kind == 'signal' and identity.isdigit():
                rec = db.execute('SELECT reason,note FROM recommendation_log WHERE id=?', (int(identity),)).fetchone()
                if rec:
                    push = signal_push(rec['note'].split(' → ')[0], rec['reason'].removeprefix('signal_'), cfg)
            if not push:
                cancelled += db.execute("UPDATE alert_delivery SET status='cancelled',last_error='Filtered by news alert policy' "
                                        "WHERE alert_id=? AND channel!='log' AND status='pending' AND lease_until=0",
                                        (row['id'],)).rowcount
    return cancelled


def quote_record(record, quote, cfg, now=None):
    """Reprice a COPY using a validated quote; never rewrite statements or daily candle history."""
    now = now or config.now_ist()
    try:
        stamp = dt.datetime.fromisoformat(quote['time'])
        price = quote['price']
        if stamp.tzinfo is None or isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError('invalid quote')
        if quote.get('symbol') != record['symbol']:
            raise ValueError('quote symbol does not match')
        age = (now-stamp).total_seconds()
        if age < 0:
            raise ValueError('future quote')
        during_market = now.weekday() < 5 and dt.time(9, 15) <= now.time().replace(tzinfo=None) <= dt.time(15, 45)
        limit = settings(cfg)['quote_max_age_minutes']*60 if during_market else 4*86400
        if age > limit:
            raise ValueError('quote is outside the freshness limit')
        saved_day = str(record.get('meta', {}).get('price_date') or record.get('meta', {}).get('market_cap_date') or '')[:10]
        if saved_day and stamp.date().isoformat() < saved_day:
            raise ValueError('quote is older than the saved price')
        from .financial_math import current_shares
        shares, _ = current_shares(record)
        if isinstance(shares, bool) or not isinstance(shares, (int, float)) or not math.isfinite(shares) or shares <= 0:
            raise ValueError('valid share count unavailable')
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('invalid quote or share count') from exc
    observed = copy.deepcopy(record)
    observed.setdefault('meta', {}).update(price=price, market_cap=price*shares,
                                         price_date=stamp.date().isoformat(), market_cap_date=stamp.date().isoformat(),
                                         price_source='Yahoo indicative quote x shares', price_time=stamp.isoformat())
    return observed


def evidence(item, record, st, indicative=False, size_rank=None, algorithm=None):
    """Freeze input identity independently of price, for honest price-versus-input attribution."""
    inputs = copy.deepcopy(record or {})
    # Advisory worksheets and refresh bookkeeping cannot waive live quote confirmation.
    for key in ('valuation_research', 'refresh_provenance', 'imported_at', 'quarter_fetched_at'):
        inputs.pop(key, None)
    meta = inputs.setdefault('meta', {})
    for key in ('price', 'market_cap', 'price_date', 'market_cap_date', 'price_source', 'price_time'):
        meta.pop(key, None)
    from .recommendations import _clean
    identity = json.dumps(_clean({'financials':inputs, 'settings':st, 'size_rank':size_rank, 'algorithm':algorithm, 'held':item['held'],
                                 'shortlisted':item['shortlisted']}), sort_keys=True)
    return {'price_snapshot':copy.deepcopy((record or {}).get('meta', {})),
            'input_fingerprint':hashlib.sha256(identity.encode()).hexdigest(), 'indicative':indicative}


def confirmed(db, symbol, prior, current, context, cfg):
    """Persist confirmation across restarts; identical/backwards quote samples cannot count twice."""
    row = db.execute('SELECT * FROM signal_evidence WHERE symbol=?', (symbol,)).fetchone()
    previous = json.loads(row['snapshot']) if row else {}
    sample = context.get('price_snapshot', {}).get('price_time')
    day = context.get('price_snapshot', {}).get('price_date')
    prior_day = previous.get('price_snapshot', {}).get('price_date')
    if day and prior_day and str(day)[:10] < str(prior_day)[:10]:
        # A failed daily refresh must not revert a newer live observation to an older saved close.
        return False, previous
    same_inputs = previous.get('input_fingerprint') == context.get('input_fingerprint')
    if prior is not None and context.get('indicative') and sample:
        last = row['last_sample'] if row else None
        if last and (dt.datetime.fromisoformat(sample) < dt.datetime.fromisoformat(last) or
                     (sample == last and same_inputs and prior != current)):
            return False, previous
    candidate = hashlib.sha256(json.dumps([current, context.get('input_fingerprint')]).encode()).hexdigest()
    count = (row['confirmations']+1 if row and row['candidate'] == candidate else 1)
    ready = (prior is None or prior == current or not context.get('indicative') or
             (bool(previous) and not same_inputs) or count >= settings(cfg)['confirmations'])
    snapshot = json.dumps(context if ready else previous)
    db.execute('INSERT INTO signal_evidence VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET '
               'snapshot=excluded.snapshot,candidate=excluded.candidate,confirmations=excluded.confirmations,last_sample=excluded.last_sample',
               (symbol, snapshot, None if ready else candidate, 0 if ready else count, sample))
    return ready, previous


def related_headlines(db, symbol, context, cfg, now=None):
    """Only dated, substantive news preceding the observed price can be associated with a crossing."""
    now = now or config.now_ist()
    meta = context.get('price_snapshot', {})
    try:
        end = dt.datetime.fromisoformat(meta['price_time']) if meta.get('price_time') else dt.datetime.combine(
            dt.date.fromisoformat(str(meta['price_date'])[:10]), config.MARKET_CLOSE, config.IST)
        if end.tzinfo is None or end > now:
            return []
    except (KeyError, ValueError, TypeError):
        return []
    start = now-dt.timedelta(hours=settings(cfg)['correlation_hours'])
    result, seen = [], set()
    # A timestamp range using julianday avoids depending on timezone string sort order.
    rows = db.execute('SELECT * FROM news WHERE symbol=? AND julianday(published_utc) BETWEEN julianday(?) '
                      'AND julianday(?) ORDER BY julianday(published_utc) DESC LIMIT 100',
                      (symbol, start.isoformat(), end.isoformat())).fetchall()
    for raw in rows:
        head = dict(raw)
        try:
            published = dt.datetime.fromisoformat(head['published_utc'])
            if published.tzinfo is None:
                continue
            head['categories'] = json.loads(head['categories'])
        except (ValueError, TypeError):
            continue
        triage = materiality.assess(head)
        if triage['label'] not in ('investment review', 'urgent review'):
            continue
        from .tracker import title_key
        key = title_key(head['title'])
        if key in seen:
            continue
        seen.add(key)
        result.append({**head, 'priority':triage['label'], 'source_type':triage['source_type']})
    # Display the original exchange disclosure before secondary reporting, even when reporting is newer.
    result.sort(key=lambda h: h['source'] != 'NSE corporate announcement')
    return result[:3]


def transition_detail(item, record, analysis, st, previous, context, prior, current, size_rank=None):
    """Estimate the crossed price boundary only when the financial/settings identity is unchanged."""
    meta = context.get('price_snapshot', {})
    old_price = previous.get('price_snapshot', {}).get('price')
    price = meta.get('price')
    result = {'cause':'inputs or data availability changed', 'threshold_price':None}
    if (not record or not previous.get('input_fingerprint') or
            previous['input_fingerprint'] != context.get('input_fingerprint')):
        return result
    if not all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0 for p in (old_price, price)) or old_price == price:
        result['cause'] = 'model status changed; price crossing unconfirmed'
        return result
    from . import recommendations, scoring

    def at(p):
        altered = {**record, 'meta':{**record['meta'], 'price':p,
                                    'market_cap':record['meta']['market_cap']*p/price}}
        return recommendations.assess(item['held'], item['shortlisted'], scoring.score_company(altered, st, size_rank), st)[0]

    if at(old_price) != prior or at(price) != current:
        return result
    left, right = old_price, price
    for _ in range(16):
        mid = (left+right)/2
        if at(mid) == prior:
            left = mid
        else:
            right = mid
    result.update(cause='price crossed a model threshold', threshold_price=right)
    return result


def recommendation_text(item, analysis, why, prior, current, context, previous, heads, detail, cfg):
    meta = context.get('price_snapshot', {})
    price = meta.get('price')
    lines = [f"Model: {prior.upper()} → {current.upper()}",
             'You hold this stock.' if item['held'] else 'On your shortlist; no recorded holding.']
    if isinstance(price, (int, float)) and math.isfinite(price):
        lines.append(f"Price: ₹{price:,.2f} as of {meta.get('price_time') or meta.get('price_date') or 'unknown'}")
    lines.append(f"Source: {meta.get('price_source') or 'saved financial record'}")
    if context.get('indicative'):
        lines.append(f"Indicative, possibly delayed quote; {settings(cfg)['confirmations']} distinct samples required for a change.")
    lines.append(f"Model version: {(analysis or {}).get('algorithm_version','unknown')} · production legacy policy. Experimental comparisons are excluded.")
    lines.append(f"Why: {why}")
    note = (analysis or {}).get('valuation',{}).get('note')
    if note:
        lines.append('Valuation assumptions: '+note)
    before = (previous or {}).get('model',{}).get('criteria',{})
    after = context.get('model',{}).get('criteria',{})
    changed = [key for key,value in after.items() if key in before and before[key] != value]
    if changed:
        lines.append('Changed criteria: '+', '.join(changed[:8]))
    if detail.get('threshold_price'):
        lines.append(f"Crossed boundary: about ₹{detail['threshold_price']:,.2f}, with financials and settings held fixed.")
    else:
        lines.append(f"Trigger: {detail['cause']}.")
    mult = (analysis or {}).get('valuation', {}).get('multiple')
    if isinstance(mult, (int, float)) and math.isfinite(mult):
        lines.append(f"Price / estimated value: {mult:.2f}x; margin of safety: {1-mult:.1%}.")
    scores = [f"{k.title()} {analysis[k]['score']:.0f}%" for k in ('buffett', 'munger', 'lynch')
              if analysis and isinstance(analysis.get(k, {}).get('score'), (int, float))]
    if scores:
        lines.append('Scores: '+'; '.join(scores)+'.')
    if heads:
        lines.append('Recent relevant news (possible context; causation unproven):')
        for h in heads:
            lines.append(f"{h['title'][:350]}\n{h['source']} · {h['published_utc']}\n{h['url'][:650]}")
        lines.append('News has not been converted into new earnings or valuation assumptions. Verify the original disclosures.')
    else:
        lines.append('No eligible recent news preceded this observed price. This model change is not attributed to a headline.')
    lines.append('Model research recommendation; review before acting. No trade executed.')
    title = f"{item['symbol']}: model {current.upper()}" if current in ('buy', 'sell') else f"{item['symbol']}: {prior.upper()} withdrawn → {current.upper()}"
    return title, '\n'.join(lines)
