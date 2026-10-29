"""Exposure of the recorded equity portfolio; cash and unrecorded assets are excluded."""
import datetime as dt
import math

from . import config, fundamentals, portfolio


def report():
    rows, sectors, issues = [], {}, []
    cfg = config.load_config()
    limits = cfg.get('risk', {})
    positions = portfolio.holdings()
    for symbol, position in positions.items():
        record = fundamentals.load(symbol) or {}
        meta = record.get('meta') or {}
        price, date = meta.get('price'), meta.get('price_date')
        valid = isinstance(price, (int,float)) and math.isfinite(price) and price > 0
        value = float(position['quantity'])*price if valid else None
        sector = record.get('industry') or record.get('screener_industry') or 'Unclassified'
        try:
            age = (config.now_ist().date()-dt.date.fromisoformat(date)).days
            stale = age < 0 or age > 7
        except (ValueError, TypeError):
            stale = True
        if not valid or stale:
            issues.append(f'{symbol}: '+('missing price' if not valid else 'stale/undated price'))
        if sector == 'Unclassified':
            issues.append(f'{symbol}: industry not classified')
        rows.append({'symbol':symbol, 'quantity':float(position['quantity']), 'price':price if valid else None,
                     'price_date':date, 'value':value, 'industry':sector, 'stale':stale})
    total = sum(r['value'] or 0 for r in rows)
    complete = bool(rows) and all(r['value'] is not None and not r['stale'] for r in rows)
    for row in rows:
        row['weight'] = row['value']/total if total and row['value'] is not None else None
        row['over_limit'] = bool(complete and limits.get('max_stock_weight') and row['weight'] > limits['max_stock_weight'])
        if row['value'] is not None:
            sectors[row['industry']] = sectors.get(row['industry'],0)+row['value']
    sector_rows = [{'industry':k, 'value':v, 'weight':v/total if total else None,
                    'over_limit':bool(complete and not any(r['industry']=='Unclassified' for r in rows)
                    and total and limits.get('max_sector_weight') and v/total>limits['max_sector_weight'])} for k,v in sectors.items()]
    return {'holdings':sorted(rows,key=lambda r:-(r['value'] or 0)),
            'industries':sorted(sector_rows,key=lambda r:-r['value']), 'known_value':total,
            'complete_prices':complete,'issues':issues,'limits':limits,
            'price_coverage':{'total_positions':len(rows),'priced_positions':sum(r['value'] is not None for r in rows),
                              'fresh_positions':sum(r['value'] is not None and not r['stale'] for r in rows)},
            'history':stable_holdings_history(positions,cfg),
            'note':'Weights cover recorded equities with available saved prices, excluding cash and unrecorded assets. Missing or stale prices disable limit flags. Industry groupings are not a complete measure of correlated risk.'}


def stable_holdings_history(positions, cfg, transactions=None):
    """Return/risk only after the last recorded transaction, with unchanged quantities.
    This avoids inventing cash balances or historical holdings. Adjusted prices give a
    reinvested-distribution approximation for each equity; it is not whole-account NAV.
    """
    import statistics
    from . import research
    transactions = portfolio.transactions() if transactions is None else transactions
    if not positions or not transactions:
        return {'status':'unavailable', 'reason':'No recorded holdings/transaction history'}
    after = max(t['trade_date'] for t in transactions)
    bench, _ = research.cached(cfg['benchmark'],'NSE',cfg)
    now = config.now_ist()
    cutoff = now.date() if now.hour >= config.EOD_READY_HOUR else now.date()-dt.timedelta(days=1)
    dates = sorted({b['date'] for b in bench if after < b['date'] <= cutoff.isoformat()})
    if len(dates) < 21:
        return {'status':'unavailable','reason':'Need 21 completed sessions after the latest ledger transaction','after':after}
    dates=dates[-253:]
    bm={b['date']:b.get('adjclose') for b in bench}
    total=[0.0]*len(dates)
    for symbol,p in positions.items():
        bars,_=research.cached(symbol,p.get('exchange','NSE'),cfg)
        if len({b['date'] for b in bars}) != len(bars):
            return {'status':'unavailable','reason':f'{symbol}: duplicate historical sessions'}
        by={b['date']:b for b in bars}
        if any(d not in by or any(not isinstance(by[d].get(k),(int,float)) or not math.isfinite(by[d][k]) or by[d][k]<=0 for k in ('close','adjclose')) for d in dates):
            return {'status':'unavailable','reason':f'{symbol}: incomplete adjusted history'}
        # Anchor to the last unadjusted mark, so dividend adjustment scales across stocks agree.
        scale=float(p['quantity'])*by[dates[-1]]['close']/by[dates[-1]]['adjclose']
        for i,d in enumerate(dates):total[i]+=scale*by[d]['adjclose']
    if any(not isinstance(bm[d],(int,float)) or not math.isfinite(bm[d]) or bm[d]<=0 for d in dates):
        return {'status':'unavailable','reason':'Invalid benchmark history'}
    returns=[math.log(b/a) for a,b in zip(total,total[1:])]
    peak=total[0];drawdown=0
    for value in total:
        peak=max(peak,value);drawdown=min(drawdown,value/peak-1)
    stock_return=total[-1]/total[0]-1
    benchmark_return=bm[dates[-1]]/bm[dates[0]]-1
    return {'status':'observed','from':dates[0],'to':dates[-1],'sessions':len(returns),
            'equity_return':stock_return,'benchmark':cfg['benchmark'],'benchmark_return':benchmark_return,
            'excess_return':stock_return-benchmark_return,'max_drawdown':drawdown,
            'annualized_volatility':statistics.stdev(returns)*math.sqrt(252),
            'note':'Unchanged recorded holdings since the last ledger transaction; at most 252 sessions. Adjusted-close distribution approximation, excluding cash, fees and unrecorded assets. This is not whole-account performance. Requires accurate recorded corporate actions; stock and index dividend conventions may differ.'}
