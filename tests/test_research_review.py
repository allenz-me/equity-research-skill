import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from research_review import review_issues


def example():
    return json.loads((ROOT / 'references/research-review-example.json').read_text())


def codes(cfg):
    return {i['code'] for i in review_issues(cfg, require_review=True)}


def hard_issues(cfg):
    return [i for i in review_issues(cfg, require_review=True) if i['severity'] in ('P0', 'P1')]


def declining(cfg):
    for period in cfg['research_review']['baseline']['periods']:
        period['profit'] = 9
        period['prior_profit'] = 15
    add_downside(cfg)


def add_downside(cfg):
    cfg['scenarios'] = [{'name': 'no-recovery', 'role': 'conditional', 'fcf': [6, 5.4]}]
    cfg['wacc'], cfg['terminal_g'] = 0.09, 0.0
    cfg['research_review']['downside'] = {
        'model_path': '/scenarios/0', 'description': 'No recovery and continuing cash flow decline.'}


def growth_example():
    cfg = example()
    cfg.pop('epv')
    cfg.update(wacc=0.10, terminal_g=0.0, scenarios=[{
        'name': 'base', 'revenue': [100, 150, 225], 'fcf_margin': [.1, .1, .1],
        'prob': 1.0, 'probability_rationale': 'Supported operating scenario.'}])
    review = cfg['research_review']
    review.pop('earnings_bridge')
    review['decision']['model_paths'] = ['/scenarios/0']
    claim = review['claims'][0]
    claim.update(kind='growth_persistence', parameter='/scenarios/0/revenue',
                 assumed_value=[100, 150, 225], conservative_value=[100, 105, 110])
    claim['benchmark'].update(industry='software', scale='small revenue cohort',
                              business_model='subscription', stage='scaling',
                              order_visibility='contract backlog and cash receipts')
    return cfg


class EvidenceReviewTests(unittest.TestCase):
    def test_example_passes_without_empirical_percentile(self):
        self.assertEqual(review_issues(example(), True), [])

    def test_legacy_config_is_not_certified(self):
        for required, severity in [(False, 'P2'), (True, 'P1')]:
            issues = review_issues({'epv': {'normalized_earnings': 100}}, required)
            self.assertEqual(issues[0]['severity'], severity)
            self.assertEqual(issues[0]['code'], 'RESEARCH_REVIEW_NOT_PERFORMED')

    def test_decline_screen_boundary_requires_no_recovery_case(self):
        cfg = example()
        for p in cfg['research_review']['baseline']['periods']:
            p['profit'] = 12  # exactly 20% below 15
        self.assertIn('EARNINGS_RECOVERY_REVIEW_TRIGGERED', codes(cfg))
        self.assertIn('REVIEW_DOWNSIDE_MISSING', codes(cfg))
        add_downside(cfg)
        self.assertEqual(hard_issues(cfg), [])

    def test_profitable_past_is_not_enough_to_restore_earnings(self):
        cfg = example()
        declining(cfg)
        claim = cfg['research_review']['claims'][0]
        claim['status'] = 'unsupported'
        claim['evidence'] = {}
        self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_cannot_rename_recovery_target_as_conservative_baseline(self):
        cfg = example()
        claim = cfg['research_review']['claims'][0]
        claim.update(status='unknown', conservative_value=90)
        self.assertIn('REVIEW_RECOVERY_BASELINE_INFLATED', codes(cfg))
        self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_claim_kind_cannot_hide_recovery(self):
        cfg = example()
        cfg['research_review']['claims'][0]['kind'] = 'growth_persistence'
        self.assertIn('REVIEW_CLAIM_KIND', codes(cfg))

    def test_labels_cannot_replace_observations_or_source_links(self):
        cfg = example()
        claim = cfg['research_review']['claims'][0]
        claim['evidence'] = {'business': 'strong', 'cash_flow': [], 'competition': [{'observation': 'share'}]}
        self.assertIn('REVIEW_EVIDENCE_MISSING', codes(cfg))
        self.assertIn('REVIEW_SOURCE_UNLINKED', codes(cfg))

    def test_earnings_bridge_reconciles_both_baseline_and_target(self):
        for key, value, code in [
            ('current_revenue', 500, 'EPV_BRIDGE_BASELINE_MISMATCH'),
            ('target_margin', .20, 'EPV_EARNINGS_BRIDGE_MISMATCH'),
            ('earnings_basis', 'NET_INCOME', 'EPV_BRIDGE_BASIS_MISMATCH'),
        ]:
            with self.subTest(key=key):
                cfg = example()
                cfg['research_review']['earnings_bridge'][key] = value
                self.assertIn(code, codes(cfg))

    def test_growth_capex_cannot_be_added_back_to_nopat(self):
        cfg = example()
        bridge = cfg['research_review']['earnings_bridge']
        bridge['target_margin'] = .1
        bridge['adjustments'] = [{'kind': 'growth_capex', 'amount': 30,
                                  'description': 'Add growth spending to NOPAT', 'source_ids': ['demo']}]
        self.assertIn('EPV_BRIDGE_ADJUSTMENT', codes(cfg))

    def test_recovery_requires_maintenance_and_cash_conversion(self):
        for key in ('maintenance_capex', 'cash_conversion'):
            with self.subTest(key=key):
                cfg = example()
                cfg['research_review']['earnings_bridge'].pop(key)
                self.assertIn('EPV_RECOVERY_BRIDGE_INCOMPLETE', codes(cfg))

    def test_seasonal_quarterly_fall_does_not_trigger_yoy_decline(self):
        cfg = example()
        first, second = cfg['research_review']['baseline']['periods']
        first.update(profit=20, prior_profit=19)
        second.update(profit=10, prior_profit=9)
        self.assertNotIn('EARNINGS_RECOVERY_REVIEW_TRIGGERED', codes(cfg))
        self.assertEqual(hard_issues(cfg), [])

    def test_mislabeled_qoq_comparison_is_rejected(self):
        cfg = example()
        period = cfg['research_review']['baseline']['periods'][1]
        period.update(prior_start='2026-01-01', prior_end='2026-03-31')
        self.assertIn('REVIEW_COMPARABLE_PERIODS', codes(cfg))

    def test_overlapping_cumulative_periods_are_rejected(self):
        cfg = example()
        period = cfg['research_review']['baseline']['periods'][1]
        period.update(start='2026-01-01', prior_start='2025-01-01')
        self.assertIn('REVIEW_OVERLAPPING_PERIODS', codes(cfg))

    def test_loss_transition_and_structural_signals_independently_trigger(self):
        for mode in ('loss', 'structural'):
            with self.subTest(mode=mode):
                cfg = example()
                if mode == 'loss':
                    cfg['research_review']['baseline']['periods'][1]['profit'] = -1
                else:
                    cfg['research_review']['structural_signals'] = [
                        {'description': 'Customer churn and pricing power deteriorated.', 'source_ids': ['demo']}]
                self.assertIn('EARNINGS_RECOVERY_REVIEW_TRIGGERED', codes(cfg))
                self.assertIn('REVIEW_DOWNSIDE_MISSING', codes(cfg))

    def test_negative_and_incomparable_bases_require_explicit_review(self):
        for mode in ('negative', 'incomparable'):
            with self.subTest(mode=mode):
                cfg = example()
                p = cfg['research_review']['baseline']['periods'][0]
                if mode == 'negative':
                    p.update(profit=-2, prior_profit=-1, comparability_note='Loss widened; percentage not meaningful.')
                else:
                    p.update(comparable=False, comparability_note='Unrestated acquisition scope.')
                self.assertIn('REVIEW_TREND_UNCERTAIN', codes(cfg))
                self.assertIn('REVIEW_DOWNSIDE_MISSING', codes(cfg))

    def test_threshold_override_requires_rationale_and_cannot_hide_structural_risk(self):
        cfg = example()
        review = cfg['research_review']
        review['decline_threshold'] = .50
        self.assertIn('REVIEW_THRESHOLD_RATIONALE', codes(cfg))
        review['threshold_rationale'] = 'Industry-specific reporting volatility.'
        review['structural_signals'] = [{'description': 'Loss of a key customer.', 'source_ids': ['demo']}]
        self.assertIn('EARNINGS_RECOVERY_REVIEW_TRIGGERED', codes(cfg))

    def test_supported_high_growth_has_no_uniform_growth_cap(self):
        self.assertEqual(hard_issues(growth_example()), [])

    def test_numeric_percentile_needs_traceable_distribution(self):
        cfg = growth_example()
        benchmark = cfg['research_review']['claims'][0]['benchmark']
        benchmark['percentile'] = 85
        self.assertIn('REVIEW_UNSUPPORTED_PERCENTILE', codes(cfg))
        benchmark['distribution'] = {'dataset': 'Synthetic cohort', 'sample_size': 100,
                                     'period': '2015-2025', 'metric': 'Revenue CAGR', 'source_ids': ['demo']}
        self.assertEqual(hard_issues(cfg), [])

    def test_assumption_claim_must_match_actual_forecast(self):
        cfg = growth_example()
        cfg['research_review']['claims'][0]['assumed_value'] = [100, 120, 144]
        self.assertIn('REVIEW_PARAMETER_MISMATCH', codes(cfg))
        self.assertIn('REVIEW_KEY_ASSUMPTION_MISSING', codes(cfg))

    def test_unsupported_upside_is_permitted_only_as_conditional(self):
        cfg = example()
        growth = growth_example()
        sc = growth['scenarios'][0]
        sc.update(role='conditional', prob=.9)
        cfg['scenarios'] = [sc]
        claim = growth['research_review']['claims'][0]
        claim.update(id='conditional-growth', status='unknown', use='conditional', evidence={})
        cfg['research_review']['claims'].append(claim)
        self.assertEqual(hard_issues(cfg), [])
        sc['role'] = 'decision'
        self.assertIn('REVIEW_USE_MISMATCH', codes(cfg))
        self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))
        self.assertIn('REVIEW_WEIGHTED_MODEL_OMITTED', codes(cfg))

    def test_conditional_model_cannot_be_selected_for_decision(self):
        cfg = example()
        cfg['epv']['role'] = 'conditional'
        cfg['research_review']['claims'][0]['use'] = 'conditional'
        self.assertIn('REVIEW_CONDITIONAL_IN_DECISION', codes(cfg))

    def test_reverse_dcf_does_not_count_as_independent_support(self):
        cfg = example()
        cfg['reverse'] = {'interim_fcf': [6, 7]}
        cfg['research_review']['decision']['model_paths'] = ['/reverse']
        self.assertIn('REVIEW_DECISION_MODELS', codes(cfg))

    def test_downside_cannot_be_a_renamed_growth_case(self):
        cfg = example()
        declining(cfg)
        cfg['scenarios'][0]['fcf'] = [6, 10]
        self.assertIn('REVIEW_DOWNSIDE_NOT_DECLINING', codes(cfg))

    def test_downside_uses_full_fade_path(self):
        cfg = example()
        declining(cfg)
        cfg['scenarios'][0].update(fade_years=5, fade_g_start=1.0)
        cfg['terminal_g'] = .02
        self.assertIn('REVIEW_DOWNSIDE_NOT_DECLINING', codes(cfg))

    def test_omitted_scalar_decision_models_cannot_escape_review(self):
        for method, block in [('eva', {'invested_capital': 100, 'nopat': 10000}),
                              ('montecarlo', {'growth_mean': .5, 'margin_mode': .5})]:
            with self.subTest(method=method):
                cfg = example()
                cfg[method] = block
                self.assertIn('REVIEW_DECISION_MODEL_OMITTED', codes(cfg))

    def test_pure_conditional_calculation_can_withhold_decision(self):
        cfg = example()
        cfg.pop('range_low')
        cfg.pop('range_high')
        cfg['epv']['role'] = 'conditional'
        claim = cfg['research_review']['claims'][0]
        claim.update(use='conditional', status='unsupported', evidence={})
        cfg['research_review']['decision'] = {'action': 'none', 'model_paths': []}
        self.assertEqual(hard_issues(cfg), [])
        cfg['research_review']['decision']['action'] = 'buy'
        self.assertIn('REVIEW_DECISION_MODELS', codes(cfg))

    def test_conditional_only_cannot_create_decision_range(self):
        cfg = example()
        cfg['epv']['role'] = 'conditional'
        cfg['research_review']['claims'][0]['use'] = 'conditional'
        cfg['research_review']['decision'] = {'action': 'none', 'model_paths': []}
        self.assertIn('REVIEW_CONDITIONAL_DECISION_RANGE', codes(cfg))


if __name__ == '__main__':
    unittest.main()
