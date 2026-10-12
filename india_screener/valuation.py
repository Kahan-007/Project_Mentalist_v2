"""Explicit FCFF/WACC and FCFE/cost-of-equity research worksheet; never silently used by signals."""
import math


def calculate(inputs):
    kind = inputs.get('kind')
    if kind not in ('FCFF','FCFE'):
        raise ValueError('Choose FCFF or FCFE')
    values = {}
    for key in ('cash_flow_cr','discount_rate','growth','terminal_growth','shares_cr'):
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
