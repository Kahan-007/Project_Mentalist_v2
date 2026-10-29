"""Explicit FCFF/WACC and FCFE/cost-of-equity research worksheet; never silently used by signals."""
import math


def calculate(inputs):
    kind = inputs.get('kind')
    if kind not in ('FCFF','FCFE'):
        raise ValueError('Choose FCFF or FCFE')
    values = {}
    for key in ('cash_flow_cr','discount_rate','growth','terminal_growth','shares_cr'):
        if inputs.get(key) in (None, '') or isinstance(inputs.get(key), bool):
            raise ValueError(f'{key} is required and must be numeric')
        value = float(inputs[key])
        if not math.isfinite(value):
            raise ValueError('Inputs must be finite numbers')
        values[key] = value
    if values['cash_flow_cr'] <= 0 or values['shares_cr'] <= 0:
        raise ValueError('This worksheet requires positive normalized cash flow and shares')
    r, g, gt = values['discount_rate'], values['growth'], values['terminal_growth']
    if not (0 < r <= .5 and -.5 <= g <= .5 and 0 <= gt < r):
        raise ValueError('Discount rate must be 0–50%, growth -50–50%, and terminal growth nonnegative and below discount rate')
    debt = cash = 0
    if kind == 'FCFF':
        if any(inputs.get(k) in (None, '') or isinstance(inputs.get(k), bool) for k in ('debt_cr','cash_cr')):
            raise ValueError('FCFF requires explicit debt and surplus cash')
        debt, cash = float(inputs['debt_cr']), float(inputs['cash_cr'])
        if any(not math.isfinite(v) or v < 0 for v in (debt,cash)):
            raise ValueError('FCFF requires nonnegative debt and surplus cash')
    cf, pv = values['cash_flow_cr'], 0
    for year in range(1,11):
        cf *= 1+g
        pv += cf/(1+r)**year
    terminal_pv = cf*(1+gt)/(r-gt)/(1+r)**10
    capital_value = pv+terminal_pv
    equity_value = capital_value-debt+cash
    return {'kind':kind,'equity_value_cr':equity_value,'value_per_share':equity_value/values['shares_cr'],
            'explicit_cash_flow_pv_cr':pv,'terminal_value_pv_cr':terminal_pv,
            'terminal_share':terminal_pv/capital_value, 'discount_basis':'WACC' if kind=='FCFF' else 'cost of equity',
            'note':'Research only. FCFF must be cash flow before debt service and discounted at WACC; subtract debt and add only surplus cash. FCFE includes financing flows and is discounted at cost of equity without a second debt deduction. Ten explicit forecast years; constant terminal growth. No active signal is changed.'}


MODEL_VERSION = 'cash-flow-scenarios-v1'


def normalized_cash_flow(inputs):
    """Calculate reviewed FCFF or FCFE from explicit components, never from an unclassified CFO."""
    def number(key):
        raw = inputs.get(key)
        if isinstance(raw, bool) or raw is None:
            raise ValueError(f'{key} is required')
        v = float(raw)
        if not math.isfinite(v):
            raise ValueError(f'{key} must be finite')
        return v
    dep, capex, working = (number(k) for k in ('depreciation_cr','capex_cr','change_noncash_working_capital_cr'))
    if dep < 0 or capex < 0:
        raise ValueError('Depreciation and gross capex must be nonnegative')
    if inputs.get('kind') == 'FCFF':
        tax = number('tax_rate')
        if not 0 <= tax <= 1:
            raise ValueError('Tax rate must be between 0 and 1')
        return number('operating_ebit_cr')*(1-tax)+dep-capex-working
    if inputs.get('kind') == 'FCFE':
        return number('net_income_cr')+dep-capex-working+number('net_borrowing_cr')
    raise ValueError('Choose FCFF or FCFE')


def scenarios(inputs, market_cap=None):
    """Transparent parameter sensitivity, not probability-weighted targets or an approved signal model."""
    from . import config
    import datetime as dt
    if not isinstance(inputs, dict) or inputs.get('reviewed') is not True or not str(inputs.get('source_note','')).strip():
        raise ValueError('Confirm reviewed normalized inputs and provide their source/normalization notes')
    try:
        as_of = dt.date.fromisoformat(inputs['as_of'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('Input evidence date must be YYYY-MM-DD') from None
    age = (config.now_ist().date()-as_of).days
    if age < 0 or age > 450:
        raise ValueError('Input evidence is future-dated or older than 450 days')
    base = dict(inputs)
    if 'components' in inputs:
        base['cash_flow_cr'] = normalized_cash_flow({**inputs['components'], 'kind':inputs.get('kind')})
    central = calculate(base)
    # Shifts are declared sensitivity assumptions, not estimates inferred from historical CAGR.
    assumptions = inputs.get('cases') or {
        'conservative':{'growth':float(base['growth'])-.02,'discount_rate':float(base['discount_rate'])+.02},
        'base':{},
        'optimistic':{'growth':float(base['growth'])+.02,'discount_rate':float(base['discount_rate'])-.02},
    }
    if set(assumptions) != {'conservative','base','optimistic'}:
        raise ValueError('Supply conservative, base and optimistic cases')
    result = []
    for label in ('conservative','base','optimistic'):
        changes = assumptions[label]
        if not isinstance(changes, dict) or set(changes) - {'growth','discount_rate','terminal_growth','cash_flow_cr'}:
            raise ValueError('Cases may change cash flow, growth, terminal growth and discount rate only')
        case = {**base, **changes}
        value = calculate(case)
        equity = value['equity_value_cr']
        valid_cap = isinstance(market_cap,(int,float)) and math.isfinite(market_cap) and market_cap > 0
        result.append({**value,'scenario':label, 'margin_of_safety':1-market_cap/equity if valid_cap and equity > 0 else None,
                       'assumptions':{k:case.get(k) for k in ('kind','cash_flow_cr','discount_rate','growth','terminal_growth','shares_cr','debt_cr','cash_cr')}})
    return {'model_version':MODEL_VERSION,'status':'experimental; no live signal changes','scenarios':result,
            'as_of':as_of.isoformat(),'source_note':str(inputs['source_note'])[:2000],
            'note':'Scenarios are parameter sensitivity, not probabilities or guaranteed prices. Default cases shift growth and required return by 2 percentage points in opposite directions. '
                   'Use WACC with FCFF and cost of equity with FCFE. No automatic cash-flow classification, normalization or financing forecast.',
            'needed':['Review sustainable reinvestment, growth and terminal assumptions','Verify current diluted shares and corporate actions',
                      'Review surplus cash, debt and other claims for FCFF']}


def for_record(record):
    inputs = record.get('valuation_research')
    if not inputs:
        return {'model_version':MODEL_VERSION, 'status':'unavailable', 'scenarios':[],
                'needed':['Reviewed normalized FCFF or FCFE','WACC or cost of equity','Forecast and terminal growth',
                          'Current diluted shares','Debt and surplus cash (FCFF)','Evidence date and source notes']}
    try:
        return scenarios(inputs, (record.get('meta') or {}).get('market_cap'))
    except (ValueError, KeyError, TypeError) as exc:
        return {'model_version':MODEL_VERSION,'status':'unavailable','scenarios':[], 'needed':[str(exc)]}
