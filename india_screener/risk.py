"""Exposure of the recorded equity portfolio; cash and unrecorded assets are excluded."""
import datetime as dt
import math

from . import config, fundamentals, portfolio


def report():
    rows, sectors, issues = [], {}, []
    limits = config.load_config().get('risk', {})
    for symbol, position in portfolio.holdings().items():
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
            'note':'Weights cover recorded equities with available saved prices, excluding cash and unrecorded assets. Missing or stale prices disable limit flags. Industry groupings are not a complete measure of correlated risk.'}
