import copy
import datetime as dt
import unittest
from unittest import mock
from india_screener import config, financial_math as fm, fundamentals, recommendations, scoring, valuation
from tests import fixtures_fund as fx


class FinancialExamples(unittest.TestCase):
    def test_independent_operating_returns_example(self):
        rec = {'years':['2024-03-31','2025-03-31'], 'sector_type':'general', 'series':{
            'share_capital':[100,100], 'reserves':[300,400], 'borrowings':[200,300], 'cash':[100,200],
            'pbt':[80,120], 'interest':[20,30], 'other_income':[10,50], 'tax':[20,30]}}
        # CE 600->800; invested capital 500->600. Operating EBIT=100; NOPAT=75.
        result = fm.operating_returns(rec)
        self.assertAlmostEqual(result['roce'][1], .142857142857)
        self.assertAlmostEqual(result['roic'][1], .136363636364)
        self.assertIsNone(result['roic'][0])
        rec['series']['other_income'][1] = None
        self.assertIsNone(fm.operating_returns(rec)['roic'][1])
        rec['series']['operating_ebit'] = [70,100]
        self.assertAlmostEqual(fm.operating_returns(rec)['roic'][1], 75/550)
        rec['series']['tax'][1] = None
        self.assertIsNone(fm.operating_returns(rec)['roic'][1])
        rec['sector_type']='bank'
        self.assertEqual(fm.operating_returns(rec)['roce'], [None,None])

    def test_negative_and_near_zero_capital_are_unavailable(self):
        rec={'years':['2024-03-31','2025-03-31'],'series':{
            'share_capital':[10,10],'reserves':[90,90],'borrowings':[0,0],'cash':[100,100],
            'pbt':[10,10],'interest':[0,0],'other_income':[0,0],'tax':[2.5,2.5]}}
        for cash in (100,101,99.999999):
            rec['series']['cash']=[cash,cash]
            self.assertIsNone(fm.operating_returns(rec)['roic'][1])

    def test_ttm_rejects_bad_sequences_and_missing_facts(self):
        q={'dates':['2025-06-30','2025-09-30','2025-12-31','2026-03-31'],'net_profit':[10,20,30,40]}
        today=dt.date(2026,4,1)
        self.assertTrue(fm.quarterly_sequence(q,today)[0])
        for dates in ([*q['dates'][:-1],'2026-06-30'], list(reversed(q['dates'])),
                      [q['dates'][0],q['dates'][0],*q['dates'][2:]],
                      ['2025-06-15','2025-09-15','2025-12-15','2026-03-15']):
            self.assertFalse(fm.quarterly_sequence({**q,'dates':dates},today)[0])
        self.assertFalse(fm.quarterly_sequence({**q,'net_profit':[10,20,None,40]},today)[0])
        self.assertFalse(fm.quarterly_sequence({**q,'period_months':[3,3,6,3]},today)[0])

    def test_share_recovery_does_not_backfill_current_face_value(self):
        rec={'years':['2023-03-31','2024-03-31','2025-03-31'],'meta':{'face_value':1},'series':{
            'share_capital':[10,10,10],'face_value_row':[10,5,None], 'shares_cr':[2,2,None]}}
        result=fm.share_evidence(rec)
        self.assertEqual(result['outstanding_shares_cr'],[1,2,None])
        self.assertEqual(result['adjusted_shares_cr'],[2,2,None])
        rec['series']['shares_count']=[2e7,2e7,None] # capital/face conflict in first year
        self.assertIsNone(fm.share_evidence(rec)['outstanding_shares_cr'][0])

    def record(self):
        r=fundamentals.parse_grid(fx.to_grid(fx.quality_company(years=[f'{y}-03-31' for y in range(2017,2027)])))
        r.update(symbol='TEST',sector_type='general',basis='consolidated',quarters={})
        r['meta']['price_date']=config.now_ist().date().isoformat()
        return r

    def test_capex_missing_cwip_and_disposals_do_not_inflate_fcf(self):
        r=self.record(); r['series']['cwip'][-1]=None
        m=fundamentals.metrics(r)
        self.assertIsNone(m['capex'][-1]);self.assertIsNone(m['fcf'][-1])
        r['series']['capex_reported']=[None]*9+[25]
        m=fundamentals.metrics(r)
        self.assertEqual(m['capex'][-1],25)
        self.assertEqual(m['capex_sources'][-1],'reported')
        r['series']['capex_reported'][-1]=None;r['series']['cwip'][-1]=0;r['series']['net_block'][-1]=0
        self.assertIsNone(fundamentals.metrics(r)['capex'][-1])

    def test_unknown_flags_cannot_improve_conservative_score(self):
        criteria=[scoring.C('a','a',50,1,'',''),scoring.C('b','b',50,0,'',''),scoring.P('p','p',10,0,'','')]
        first=scoring.summarise(copy.deepcopy(criteria),scoring.DEFAULT_SETTINGS)
        criteria[1]['score']=None;criteria[2]['score']=None
        second=scoring.summarise(criteria,scoring.DEFAULT_SETTINGS)
        self.assertGreater(second['score'],first['score']) # legacy behavior exposed, not hidden
        self.assertEqual(second['conservative_score'],first['conservative_score'])
        self.assertEqual(second['unknown_red_flags'],['p'])
        self.assertEqual(second['criteria'][1]['state'],'unknown')

    def test_missing_latest_leverage_blocks_buy_without_using_old_value(self):
        r=self.record();r['series']['borrowings'][-1]=None
        score=scoring.score_company(r,scoring.DEFAULT_SETTINGS)
        self.assertEqual(recommendations.assess(False,True,score,scoring.DEFAULT_SETTINGS)[0],'review')
        criterion=next(c for c in score['buffett']['criteria'] if c['key']=='low_debt')
        self.assertEqual(criterion['state'],'unknown')

    def test_experimental_model_is_explicit_and_does_not_modify_inputs(self):
        r=self.record();before=copy.deepcopy(r)
        prod=scoring.score_company(r,scoring.DEFAULT_SETTINGS)
        exp=scoring.score_company(r,scoring.DEFAULT_SETTINGS,model='operating-v1')
        comparison=recommendations.compare(True,True,prod,exp,{'scenarios':[]},scoring.DEFAULT_SETTINGS)
        self.assertEqual(comparison['experimental_assessment'],'insufficient evidence')
        self.assertEqual(comparison['production_signal'],recommendations.assess(True,True,prod,scoring.DEFAULT_SETTINGS)[0])
        self.assertNotEqual(prod['algorithm_version'],exp['algorithm_version'])
        self.assertEqual(r,before)


class ValuationExamples(unittest.TestCase):
    def inputs(self):
        return {'kind':'FCFF','cash_flow_cr':100,'discount_rate':.1,'growth':0,'terminal_growth':0,
                'shares_cr':10,'debt_cr':200,'cash_cr':50,'reviewed':True,
                'as_of':config.now_ist().date().isoformat(),'source_note':'Reviewed test statement'}

    def test_independent_cash_flow_examples(self):
        # EBIT 200, tax 25%, dep 30, capex 60, WC investment 20 => 100 FCFF.
        components={'kind':'FCFF','operating_ebit_cr':200,'tax_rate':.25,'depreciation_cr':30,
                    'capex_cr':60,'change_noncash_working_capital_cr':20}
        self.assertEqual(valuation.normalized_cash_flow(components),100)
        # Income 120 + dep 30 - capex 60 - WC 20 + net borrowing 10 => 80 FCFE.
        self.assertEqual(valuation.normalized_cash_flow({**components,'kind':'FCFE','net_income_cr':120,'net_borrowing_cr':10}),80)
        with self.assertRaises(ValueError):valuation.normalized_cash_flow({**components,'capex_cr':None})

    def test_perpetuity_and_no_double_debt_deduction(self):
        v=valuation.calculate(self.inputs())
        self.assertAlmostEqual(v['equity_value_cr'],850) # 100/.1 - 200 + 50
        self.assertAlmostEqual(v['terminal_share'],.3855432894)
        equity=valuation.calculate({**self.inputs(),'kind':'FCFE'})
        self.assertAlmostEqual(equity['equity_value_cr'],1000)

    def test_scenarios_and_unavailable_inputs(self):
        result=valuation.scenarios(self.inputs(),500)
        self.assertEqual([r['scenario'] for r in result['scenarios']],['conservative','base','optimistic'])
        values=[r['equity_value_cr'] for r in result['scenarios']]
        self.assertLess(values[0],values[1]);self.assertLess(values[1],values[2])
        self.assertAlmostEqual(result['scenarios'][1]['margin_of_safety'],.41176470588)
        self.assertEqual(valuation.for_record({})['status'],'unavailable')
        for changes in ({'reviewed':False},{'as_of':'2099-01-01'},{'cash_cr':''},{'discount_rate':True}):
            with self.assertRaises(ValueError):valuation.scenarios({**self.inputs(),**changes})


class CyclicalComparability(unittest.TestCase):
    def metrics(self, n=10):
        return {'eps_cagr_5':.18, 'eps':[10+i for i in range(n)], 'n_years':n,
                'years':[f'{2017+i}-03-31' for i in range(n)], 'market_cap':1000,
                'price':100, 'cash':100, 'investments':0}

    def test_sector_rule_does_not_disappear_with_seventh_year(self):
        rec={'sector_type':'general','industry':'Metals & Mining'}
        for n in (6,7,10):
            m=self.metrics(n)
            self.assertEqual(scoring.lynch_category(m,rec,{},'sector-first-v1'),'cyclical')
            diagnostic=scoring.cycle_assessment(m,rec,{})
            self.assertEqual(diagnostic['observations'],n)
            self.assertEqual(diagnostic['legacy_omits_cycle_test'],n>=7)
        self.assertEqual(scoring.lynch_category(self.metrics(),rec,{}),'stalwart')

    def test_same_normalization_retains_losses_and_unknowns(self):
        rec={'sector_type':'general','industry':'Metals & Mining'}
        m=self.metrics(6);m['eps']=[-10,None,10,20,30,40];m['price']=400
        d=scoring.cycle_assessment(m,rec,{})
        self.assertEqual(d['median_eps'],20)
        self.assertEqual(d['normalised_pe'],20)
        self.assertAlmostEqual(d['criterion_score'],1/3)
        self.assertEqual(d['missing_periods'],['2018-03-31'])
        m['eps']=[-10,-5,-2,None,10,20]
        self.assertEqual(scoring.cycle_assessment(m,rec,{})['criterion_score'],0)
        m['eps']=[None,None,None,None,10,20]
        self.assertIsNone(scoring.cycle_assessment(m,rec,{})['criterion_score'])

    def test_sector_change_is_experimental_in_scores_and_dcf(self):
        r=FinancialExamples().record();r['industry']='Metals & Mining'
        prod=scoring.score_company(r,scoring.DEFAULT_SETTINGS)
        exp=scoring.score_company(r,scoring.DEFAULT_SETTINGS,model='operating-v1')
        self.assertNotEqual(prod['lynch_category'],'cyclical')
        self.assertEqual(exp['lynch_category'],'cyclical')
        self.assertTrue(any(c['key']=='normalised_pe' for c in exp['lynch']['criteria']))
        self.assertFalse(any(c['key']=='normalised_pe' for c in prod['lynch']['criteria']))
        self.assertTrue(prod['valuation']['production']);self.assertFalse(exp['valuation']['production'])
        self.assertIn('median FCF',exp['valuation']['note'])
