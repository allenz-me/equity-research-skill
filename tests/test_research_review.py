import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from research_review import review_issues, montecarlo_summary
from dcf import montecarlo_spec


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
    review['baseline']['cash_flow'] = {
        'actual': True, 'revenue': 100, 'nopat': 60, 'da': 0,
        'capex': 50, 'change_nwc': 0, 'fcff': 10, 'source_ids': ['demo']}
    margin_claim = copy.deepcopy(claim)
    margin_claim.update(id='cash-margin', kind='earnings_recovery', parameter='/scenarios/0/fcf_margin',
                        assumed_value=[.1, .1, .1], conservative_value=[.1, .1, .1], status='unknown', evidence={})
    review['claims'].append(margin_claim)
    return cfg


def direct_fcf_example():
    cfg = growth_example()
    sc = cfg['scenarios'][0]
    sc.pop('fcf_margin')
    revenues = sc.pop('revenue')
    sc['fcf'] = [10, 40, 85]
    sc['operating_bridge'] = {
        'revenue': revenues, 'nopat_margin': [.6, .6, .6],
        'da': [0, 0, 0], 'capex': [50, 50, 50], 'change_nwc': [0, 0, 0], 'source_ids': ['demo']}
    claims = cfg['research_review']['claims']
    claims[0]['parameter'] = '/scenarios/0/operating_bridge/revenue'
    claims[1].update(parameter='/scenarios/0/operating_bridge/nopat_margin',
                     assumed_value=[.6, .6, .6], conservative_value=[.6, .6, .6])
    fcf_claim = copy.deepcopy(claims[0])
    fcf_claim.update(id='fcff-path', parameter='/scenarios/0/fcf', kind='earnings_recovery',
                     assumed_value=sc['fcf'], conservative_value=[10, 10, 10])
    claims.append(fcf_claim)
    return cfg


def mc_example():
    cfg = growth_example()
    cfg.pop('scenarios')
    cfg['montecarlo'] = {
        'base_revenue': 100, 'growth_mean': 0, 'growth_std': 0,
        'margin_low': .05, 'margin_mode': .1, 'margin_high': .15,
        'years': 2, 'fade_years': 0, 'wacc_low': .1, 'wacc_high': .1}
    review = cfg['research_review']
    review['decision']['model_paths'] = ['/montecarlo']
    record = copy.deepcopy(review['claims'][0])
    review['claims'] = []
    for field in ('id', 'kind', 'parameter', 'assumed_value', 'conservative_value'):
        record.pop(field)
    spec = montecarlo_spec(cfg['montecarlo'], cfg)
    record.update(assumed_spec=spec, conservative_spec=copy.deepcopy(spec),
                  assumed_summary=montecarlo_summary(spec), conservative_summary=montecarlo_summary(spec),
                  expectation_rationale='Synthetic cohort supports the mean cash margin.',
                  tail_rationale='Synthetic tails describe supported probabilities and magnitudes, not certainty.')
    review['montecarlo_distribution'] = record
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

    def test_cash_models_need_actual_reconciled_fcff_baseline(self):
        for mutation, expected in [('missing', 'REVIEW_CASH_BASELINE_MISSING'),
                                   ('forecast', 'REVIEW_CASH_BASELINE_MISSING'),
                                   ('wrong_fcff', 'REVIEW_CASH_BASELINE_MISMATCH'),
                                   ('wrong_nopat', 'REVIEW_CASH_BASELINE_MISMATCH')]:
            with self.subTest(mutation=mutation):
                cfg = growth_example()
                baseline = cfg['research_review']['baseline']
                if mutation == 'missing':
                    baseline.pop('cash_flow')
                elif mutation == 'forecast':
                    baseline['cash_flow']['actual'] = False
                else:
                    baseline['cash_flow']['fcff' if mutation == 'wrong_fcff' else 'nopat'] = 999
                self.assertIn(expected, codes(cfg))

    def test_flat_single_and_declining_recovery_cannot_rename_actual_baseline(self):
        for margins in ([.4, .4], [.4], [.4, .3], [.05, .07]):
            with self.subTest(margins=margins):
                cfg = growth_example()
                cfg['scenarios'][0].update(revenue=[100] * len(margins), fcf_margin=margins)
                revenue, margin = cfg['research_review']['claims']
                revenue.update(assumed_value=[100] * len(margins), conservative_value=[100] * len(margins))
                margin.update(assumed_value=margins, conservative_value=margins)
                self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))
                margin.update(status='supported', evidence=copy.deepcopy(revenue['evidence']))
                self.assertEqual(hard_issues(cfg), [])

    def test_revenue_approval_cannot_cover_missing_flat_margin_claim(self):
        cfg = growth_example()
        cfg['scenarios'][0]['fcf_margin'] = [.4, .4, .4]
        cfg['research_review']['claims'].pop()
        self.assertIn('REVIEW_KEY_ASSUMPTION_MISSING', codes(cfg))

    def test_actual_revenue_cannot_be_renamed_as_conservative_growth(self):
        cfg = growth_example()
        claim = cfg['research_review']['claims'][0]
        claim.update(conservative_value=claim['assumed_value'], status='unknown', evidence={})
        self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_direct_fcff_bridge_is_required_and_reconciles_each_year(self):
        cfg = direct_fcf_example()
        self.assertEqual(hard_issues(cfg), [])
        cfg['scenarios'][0]['operating_bridge']['capex'][1] = 49
        self.assertIn('REVIEW_FCFF_BRIDGE_MISMATCH', codes(cfg))
        cfg['scenarios'][0].pop('operating_bridge')
        self.assertIn('REVIEW_FCFF_BRIDGE_MISSING', codes(cfg))

    def test_direct_fcff_recovery_cannot_hide_in_a_conservative_path(self):
        for fcfs in ([40, 40], [40], [40, 30]):
            with self.subTest(fcfs=fcfs):
                cfg = direct_fcf_example()
                count = len(fcfs)
                bridge = cfg['scenarios'][0]['operating_bridge']
                bridge.update(revenue=[100] * count, nopat_margin=[.6] * count, da=[0] * count,
                              capex=[60 - f for f in fcfs], change_nwc=[0] * count)
                cfg['scenarios'][0]['fcf'] = fcfs
                revenue, margin, fcf = cfg['research_review']['claims']
                revenue.update(assumed_value=[100] * count, conservative_value=[100] * count)
                margin.update(assumed_value=[.6] * count, conservative_value=[.6] * count)
                fcf.update(assumed_value=fcfs, conservative_value=fcfs, status='unknown', evidence={})
                self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_fade_and_terminal_growth_cannot_rename_positive_growth_as_conservative(self):
        for parameter in ('/scenarios/0/fade_g_start', '/terminal_g'):
            with self.subTest(parameter=parameter):
                cfg = direct_fcf_example()
                if parameter.endswith('fade_g_start'):
                    cfg['scenarios'][0].update(fade_years=2, fade_g_start=.4)
                    value = .4
                else:
                    cfg['terminal_g'] = value = .02
                claim = copy.deepcopy(cfg['research_review']['claims'][0])
                claim.update(id='future-growth', parameter=parameter, assumed_value=value,
                             conservative_value=value, status='unknown', evidence={})
                cfg['research_review']['claims'].append(claim)
                self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_mc_supported_distribution_and_exact_triangular_mean(self):
        cfg = mc_example()
        self.assertEqual(hard_issues(cfg), [])
        spec = cfg['research_review']['montecarlo_distribution']['assumed_spec']
        self.assertAlmostEqual(montecarlo_summary(spec)['margin_mean'], .1)
        spec = dict(spec, margin_high=.6)
        self.assertAlmostEqual(montecarlo_summary(spec)['margin_mean'], .25)

    def test_mc_empirical_percentile_requires_the_same_traceable_distribution_as_claims(self):
        cfg = mc_example()
        benchmark = cfg['research_review']['montecarlo_distribution']['benchmark']
        benchmark['percentile'] = 85
        self.assertIn('REVIEW_UNSUPPORTED_PERCENTILE', codes(cfg))
        benchmark['distribution'] = {'dataset': 'Synthetic cohort', 'sample_size': 100,
                                     'period': '2015-2025', 'metric': 'FCFF margin', 'source_ids': ['demo']}
        self.assertEqual(hard_issues(cfg), [])
        for field in ('dataset', 'sample_size', 'period', 'metric', 'source_ids'):
            with self.subTest(field=field):
                malformed = copy.deepcopy(cfg)
                malformed['research_review']['montecarlo_distribution']['benchmark']['distribution'].pop(field)
                self.assertTrue(hard_issues(malformed))

    def test_mc_growth_tail_needs_cohort_match_but_zero_growth_does_not(self):
        cfg = mc_example()
        record = cfg['research_review']['montecarlo_distribution']
        for field in ('industry', 'scale', 'business_model', 'stage', 'order_visibility'):
            record['benchmark'].pop(field)
        self.assertEqual(hard_issues(cfg), [])
        for growth_field, value in [('growth_std', .02), ('terminal_g', .02)]:
            with self.subTest(growth_field=growth_field):
                growth = copy.deepcopy(cfg)
                growth['montecarlo'][growth_field] = value
                spec = montecarlo_spec(growth['montecarlo'], growth)
                growth['research_review']['montecarlo_distribution'].update(
                    assumed_spec=spec, assumed_summary=montecarlo_summary(spec))
                self.assertIn('REVIEW_COHORT_INCOMPLETE', codes(growth))

    def test_mc_no_upside_distribution_can_remain_unknown(self):
        cfg = mc_example()
        cfg['montecarlo'].update(margin_low=.05, margin_mode=.08, margin_high=.1)
        record = cfg['research_review']['montecarlo_distribution']
        spec = montecarlo_spec(cfg['montecarlo'], cfg)
        record.update(assumed_spec=spec, conservative_spec=copy.deepcopy(spec),
                      assumed_summary=montecarlo_summary(spec), conservative_summary=montecarlo_summary(spec),
                      status='unknown', evidence={})
        self.assertEqual(hard_issues(cfg), [])

    def test_checked_in_cash_model_examples_pass_review(self):
        for filename in ('research-review-dcf-example.json', 'research-review-montecarlo-example.json'):
            with self.subTest(filename=filename):
                cfg = json.loads((ROOT / 'references' / filename).read_text())
                self.assertEqual(review_issues(cfg, True), [])

    def test_checker_script_and_package_invocations_import_cash_model_helpers(self):
        recovery = example()
        declining(recovery)
        with tempfile.TemporaryDirectory() as temp:
            for name, cfg in [('montecarlo', mc_example()), ('recovery-downside', recovery)]:
                path = Path(temp) / f'{name}.json'
                path.write_text(json.dumps(cfg))
                for entrypoint in ([str(ROOT / 'scripts/check_research_output.py')],
                                   ['-m', 'scripts.check_research_output']):
                    with self.subTest(name=name, entrypoint=entrypoint):
                        # A fresh process with ignored PYTHON* environment cannot inherit this
                        # test module's sys.path insertion and hide package-import failures.
                        result = subprocess.run([sys.executable, '-E', *entrypoint,
                                                 '--assumptions', str(path), '--json'],
                                                cwd=ROOT, capture_output=True, text=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        findings = json.loads(result.stdout)
                        self.assertFalse(any(i['severity'] in ('P0', 'P1') for i in findings), findings)

    def test_mc_full_spec_binds_boundaries_dispersion_and_other_economic_inputs(self):
        for key, value in [('margin_low', .08), ('margin_high', .6), ('growth_std', .1),
                           ('wacc_low', .09), ('years', 3), ('fade_years', 5),
                           ('annual_dilution', .01), ('base_revenue', 200)]:
            with self.subTest(key=key):
                cfg = mc_example()
                cfg['montecarlo'][key] = value
                self.assertIn('REVIEW_MC_DISTRIBUTION_MISMATCH', codes(cfg))
        cfg = mc_example()
        cfg['montecarlo'].update(n=100, seed=11)
        self.assertEqual(hard_issues(cfg), [])  # Sampling precision is not the economic distribution.

    def test_mc_default_discount_rates_are_bound_after_inheritance(self):
        cfg = mc_example()
        for key in ('wacc_low', 'wacc_high'):
            cfg['montecarlo'].pop(key)
        record = cfg['research_review']['montecarlo_distribution']
        spec = montecarlo_spec(cfg['montecarlo'], cfg)
        record.update(assumed_spec=spec, assumed_summary=montecarlo_summary(spec))
        self.assertEqual(hard_issues(cfg), [])
        cfg['wacc'] = .08
        self.assertIn('REVIEW_MC_DISTRIBUTION_MISMATCH', codes(cfg))

    def test_mc_cannot_rename_recovery_distribution_as_conservative(self):
        cfg = mc_example()
        cfg['montecarlo']['margin_high'] = .6
        record = cfg['research_review']['montecarlo_distribution']
        spec = montecarlo_spec(cfg['montecarlo'], cfg)
        record.update(assumed_spec=spec, conservative_spec=copy.deepcopy(spec),
                      assumed_summary=montecarlo_summary(spec), conservative_summary=montecarlo_summary(spec),
                      status='unknown', evidence={})
        self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_mc_growth_dispersion_needs_tail_support_even_if_mean_is_zero(self):
        cfg = mc_example()
        cfg['montecarlo'].update(growth_std=.1, margin_low=.1, margin_mode=.1, margin_high=.1)
        record = cfg['research_review']['montecarlo_distribution']
        spec = montecarlo_spec(cfg['montecarlo'], cfg)
        record.update(assumed_spec=spec, conservative_spec=copy.deepcopy(spec),
                      assumed_summary=montecarlo_summary(spec), conservative_summary=montecarlo_summary(spec),
                      status='unknown', evidence={})
        self.assertIn('REVIEW_UNSUPPORTED_DECISION_UPLIFT', codes(cfg))

    def test_mc_base_revenue_must_be_actual_even_with_supported_record(self):
        cfg = mc_example()
        cfg['montecarlo']['base_revenue'] = 200
        record = cfg['research_review']['montecarlo_distribution']
        spec = montecarlo_spec(cfg['montecarlo'], cfg)
        record.update(assumed_spec=spec, assumed_summary=montecarlo_summary(spec))
        self.assertIn('REVIEW_MC_BASELINE_MISMATCH', codes(cfg))

    def test_mc_summary_and_tail_rationale_cannot_be_omitted_or_changed(self):
        cfg = mc_example()
        record = cfg['research_review']['montecarlo_distribution']
        record['assumed_summary']['margin_mean'] = .25
        self.assertIn('REVIEW_MC_DISTRIBUTION_MISMATCH', codes(cfg))
        record.pop('tail_rationale')
        self.assertIn('REVIEW_MC_DISTRIBUTION_INCOMPLETE', codes(cfg))

    def test_conditional_cash_models_do_not_become_decision_models(self):
        for cfg, block in [(growth_example(), 'scenarios'), (mc_example(), 'montecarlo')]:
            with self.subTest(block=block):
                cfg.pop('range_low')
                cfg.pop('range_high')
                model = cfg['scenarios'][0] if block == 'scenarios' else cfg[block]
                model['role'] = 'conditional'
                review = cfg['research_review']
                review['baseline'].pop('cash_flow')
                review['claims'] = []
                review.pop('montecarlo_distribution', None)
                review['decision'] = {'action': 'none', 'model_paths': []}
                self.assertEqual(hard_issues(cfg), [])


if __name__ == '__main__':
    unittest.main()
