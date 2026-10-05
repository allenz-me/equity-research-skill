import contextlib
import copy
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(__file__))
SCRIPT = os.path.join(ROOT, "scripts", "dcf.py")
SPEC = importlib.util.spec_from_file_location("dcf", SCRIPT)
DCF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DCF)


def quiet(fn, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = fn(*args)
    return result, output.getvalue()


class LimitedLiabilityTests(unittest.TestCase):
    def test_dcf_preserves_residual_claim_but_floors_common_equity(self):
        # A perpetual FCFF of 10 at 10% has EV 100, independently of its financing.
        for debt, raw_equity, equity in ((50., 50., 50.), (100., 0., 0.), (150., -50., 0.)):
            with self.subTest(debt=debt):
                result = DCF.dcf_value({'fcf': [10.]}, .1, 0., 2., debt)
                self.assertAlmostEqual(100., result['ev'])
                self.assertAlmostEqual(raw_equity, result['raw_equity'])
                self.assertAlmostEqual(max(-raw_equity, 0.), result['equity_shortfall'])
                self.assertAlmostEqual(raw_equity / 2, result['raw_per_share'])
                self.assertAlmostEqual(equity, result['equity'])
                self.assertAlmostEqual(equity / 2, result['per_share'])

    def test_insolvent_scenario_cannot_reverse_expected_return_or_lose_over_100_percent(self):
        cfg = {'price': 40., 'shares': 1., 'net_debt': 100., 'wacc': .1, 'terminal_g': 0.,
               'scenarios': [{'name': 'bear', 'prob': .5, 'fcf': [5.]},
                             {'name': 'bull', 'prob': .5, 'fcf': [20.]}]}
        results, output = quiet(DCF.run, cfg)
        self.assertEqual(0., results['bear']['per_share'])
        self.assertAlmostEqual(-50., results['bear']['raw_equity'])
        self.assertAlmostEqual(100., results['bull']['per_share'])
        self.assertIn('概率加权公允价值: 50.0/股（较现价 +25%）', output)
        self.assertIn('概率加权期望收益 EV = +25%', output)
        self.assertIn('下行幅度（熊） -100%', output)
        self.assertIn('Kelly-lite（¼Kelly，上限15%）≈ 4%', output)
        self.assertIn('原始权益 -50.00 | 权益缺口 50.00', output)
        self.assertIn('归零仅为简化', output)

    def test_epv_and_franchise_preserve_shortfalls_in_both_earnings_bases(self):
        cfg = {'normalized_earnings': 10., 'coc': .1, 'shares': 2., 'net_debt': 150.,
               'asset_value': 50., 'price': 40., 'growth': {'g': .02, 'roiic': .2}}
        result, output = quiet(DCF.run_epv, cfg)
        self.assertAlmostEqual(100., result['ev'])
        self.assertAlmostEqual(-50., result['raw_equity'])
        self.assertAlmostEqual(50., result['equity_shortfall'])
        self.assertEqual(0., result['epv_ps'])
        self.assertEqual(0., result['growth_ps'])
        self.assertAlmostEqual(112.5, result['growth']['ev'])
        self.assertAlmostEqual(-37.5, result['growth']['raw_equity'])
        self.assertIn('成长调整 0.00', output)
        self.assertIn('成长价值：原始权益 -37.50', output)
        for fn, args in ((DCF.epv_value, (-10., .1)),
                         (DCF.franchise_growth_value, (-10., .1, .02, .2))):
            with self.subTest(fn=fn.__name__):
                value = fn(*args, basis='NET_INCOME')
                self.assertIsNone(value['ev'])
                self.assertLess(value['raw_equity'], 0.)
                self.assertEqual(0., value['equity'])
                self.assertEqual(-value['raw_equity'], value['equity_shortfall'])

    def test_eva_preserves_enterprise_value_and_equity_shortfall(self):
        result, output = quiet(DCF.run_eva,
                               {'invested_capital': 100., 'nopat': 10., 'fade_years': 2},
                               {'wacc': .1, 'shares': 2., 'net_debt': 150.})
        self.assertAlmostEqual(100., result['ev'])
        self.assertAlmostEqual(-50., result['raw_equity'])
        self.assertAlmostEqual(50., result['equity_shortfall'])
        self.assertEqual(0., result['equity'])
        self.assertEqual(0., result['per_share'])
        self.assertIn('EVA：原始权益 -50.00', output)

    def test_montecarlo_floors_each_draw_before_computing_distribution(self):
        cfg = {'n': 2, 'years': 1, 'fade_years': 0, 'base_revenue': 100.,
               'growth_mean': 0., 'growth_std': 0., 'margin_low': .05,
               'margin_mode': .1, 'margin_high': .2, 'wacc_low': .1,
               'wacc_high': .1, 'terminal_g': 0.}
        with mock.patch.object(DCF.random, 'Random') as rng_factory:
            rng = rng_factory.return_value
            rng.gauss.return_value = 0.
            rng.triangular.side_effect = [.05, .2]
            rng.uniform.return_value = .1
            result, output = quiet(DCF.run_montecarlo, cfg,
                                   {'shares': 1., 'net_debt': 100., 'price': 40.})
        self.assertAlmostEqual(50., result['mean'])
        self.assertAlmostEqual(25., result['raw_mean_per_share'])
        self.assertEqual(0., result['p10'])
        self.assertAlmostEqual(100., result['p90'])
        self.assertEqual(.5, result['p_loss'])
        self.assertEqual(1, result['equity_shortfall_count'])
        self.assertAlmostEqual(-50., result['min_raw_equity'])
        self.assertAlmostEqual(25., result['mean_equity_shortfall'])
        self.assertIn('1/2 次模拟原始权益为负并归零', output)

    def test_position_rejects_external_negative_common_share_values(self):
        cfg = {'price': 40., 'scenarios': [{'name': 'bear', 'prob': 1.}]}
        with self.assertRaisesRegex(ValueError, '普通股每股价值不得为负'):
            quiet(DCF.run_position, {'bear': {'per_share': -50.}}, cfg)


class DilutionTests(unittest.TestCase):
    def test_constant_perpetuity_is_invariant_to_stage_partition(self):
        expected = 100 / 1.1 ** 10
        for explicit in (1, 4, 10):
            for path in ({"fcf": [10.] * explicit},
                         {"revenue": [100.] * explicit, "fcf_margin": [.1] * explicit}):
                sc = dict(path, fade_years=10 - explicit, fade_g_start=0., annual_dilution=.1)
                result = DCF.dcf_value(sc, .1, 0., 1., 0.)
                with self.subTest(explicit=explicit, path=path):
                    self.assertAlmostEqual(100., result["ev"])
                    self.assertAlmostEqual(expected, result["per_share"])
                    self.assertAlmostEqual(1.1 ** 10, result["shares_end"])
                    self.assertEqual(10, result["forecast_years"])

    def test_zero_dilution_and_net_debt_bridge(self):
        result = DCF.dcf_value({"fcf": [10.]}, .1, 0., 2., 20.)
        self.assertAlmostEqual(40., result["per_share"])

    def test_montecarlo_degenerate_distribution_matches_closed_form(self):
        c = {"n": 5, "years": 1, "fade_years": 9, "base_revenue": 100.,
             "growth_mean": 0., "growth_std": 0., "margin_low": .1,
             "margin_mode": .1, "margin_high": .1, "wacc_low": .1,
             "wacc_high": .1, "annual_dilution": .1, "terminal_g": 0.}
        # Fully specified MC needs no top-level WACC; all ten years dilute shares.
        result, _ = quiet(DCF.run_montecarlo, c, {"shares": 1.})
        self.assertAlmostEqual(100 / 1.1 ** 10, result["mean"])
        self.assertAlmostEqual(result["mean"], result["p50"])


class EvaTests(unittest.TestCase):
    def eva(self, **overrides):
        c = dict(invested_capital=100., nopat=20., fade_years=2, **overrides)
        return quiet(DCF.run_eva, c, {"wacc": .1, "shares": 1.})[0]

    def test_charges_opening_capital(self):
        result = self.eva()
        self.assertAlmostEqual(114.60055096418733, result["ev"])
        self.assertAlmostEqual(10., result["rows"][0]["capital_charge"])
        self.assertAlmostEqual(10., result["rows"][0]["eva"])
        self.assertAlmostEqual(100 / .75, result["rows"][1]["begin_capital"])

    def test_independent_fcff_dcf_reconciles_across_growth_and_decline(self):
        for growth in (-.15, 0., .2):
            result = self.eva(nopat_growth=growth)
            capital = 100.
            dcf = 0.
            for row in result["rows"]:
                # Reconstruct cash distributed after investment, independently of reported EVA/FCFF.
                investment = row["end_capital"] - capital
                dcf += (row["nopat"] - investment) / 1.1 ** row["year"]
                capital = row["end_capital"]
            dcf += result["terminal_nopat"] / .1 / 1.1 ** len(result["rows"])
            with self.subTest(growth=growth):
                self.assertAlmostEqual(dcf, result["ev"])
                self.assertAlmostEqual(result["terminal_ev"] * .1, result["terminal_nopat"])
                self.assertEqual(0., result["terminal_growth"])

    def test_zero_spread_has_no_franchise_value(self):
        c = {"invested_capital": 100., "nopat": 10., "fade_years": 4, "wacc": .1}
        result, _ = quiet(DCF.run_eva, c, {})
        self.assertAlmostEqual(100., result["ev"])
        self.assertTrue(all(abs(row["eva"]) < 1e-12 for row in result["rows"]))

    def test_negative_spread_and_one_year_fade(self):
        c = {"invested_capital": 100., "nopat": 5., "fade_years": 1, "wacc": .1}
        result, _ = quiet(DCF.run_eva, c, {})
        self.assertAlmostEqual(100. - 5. / 1.1, result["ev"])
        self.assertAlmostEqual(50., result["terminal_ev"])

    def test_unsupported_negative_earnings_do_not_fabricate_capital(self):
        with self.assertRaisesRegex(ValueError, "不适用"):
            quiet(DCF.run_eva, {"invested_capital": 100., "nopat": -1., "wacc": .1}, {})


class BasisAndBridgeTests(unittest.TestCase):
    def test_epv_only_config_needs_no_dcf_fields(self):
        c = {"epv": {"normalized_earnings": 60., "coc": .1, "shares": 2.}}
        _, output = quiet(DCF.run, c)
        self.assertIn("每股 300.00", output)
        self.assertNotIn("=== 情景 DCF", output)

    def test_equity_epv_uses_explicit_coe(self):
        c = {"earnings_basis": "NET_INCOME", "normalized_earnings": 100.,
             "coc": .125, "shares": 2., "discount_rate_basis": "COE"}
        result, _ = quiet(DCF.run_epv, c)
        self.assertAlmostEqual(400., result["epv_ps"])
        self.assertIsNone(result["ev"])
        with self.assertRaises(ValueError):
            quiet(DCF.run_epv, dict(c, discount_rate_basis="WACC"))
        with self.assertRaises(ValueError):
            quiet(DCF.run_epv, dict(c, net_debt=20.))

    def test_pvgo_never_inherits_wacc(self):
        c = {"earnings_ps": 5., "price": 100.}
        with self.assertRaisesRegex(ValueError, "CoE"):
            quiet(DCF.run_pvgo, c, {"wacc": .1})
        result, _ = quiet(DCF.run_pvgo, dict(c, r=.125), {"wacc": .1})
        self.assertAlmostEqual(40., result["zero_growth_value"])
        with self.assertRaises(ValueError):
            quiet(DCF.run_pvgo, dict(c, r=.125, discount_rate_basis="WACC"), {})

    def test_equity_cashflow_cannot_enter_firm_engines(self):
        for tag in ({"cashflow_basis": "FCFE"}, {"earnings_basis": "NET_INCOME"},
                    {"valuation_basis": "equity"}, {"discount_rate_basis": "COE"}):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                DCF.dcf_value(dict(fcf=[10.], **tag), .1, 0., 1., 0.)
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                quiet(DCF.run_eva, dict(invested_capital=100., nopat=20., wacc=.1, **tag), {})

    def test_cash_is_counted_once(self):
        self.assertAlmostEqual(120., DCF.epv_value(10., .1, net_debt=-20.)["equity"])
        for cash in (20., -20., math.nan):
            with self.subTest(cash=cash), self.assertRaises(ValueError):
                DCF.epv_value(10., .1, net_debt=-20., excess_cash=cash)
        with self.assertRaises(ValueError):
            quiet(DCF.run, {"epv": {"normalized_earnings": 10., "coc": .1, "shares": 1.}, "excess_cash": 20.})

    def test_invalid_franchise_never_adds_arbitrary_growth_premium(self):
        epv = {"normalized_earnings": 10., "coc": .1, "shares": 1.}
        for growth in ({"g": .02}, {"g": .1, "roiic": .2},
                       {"g": .04, "roiic": .02}, {"g": .02, "mode": "simple"}):
            result, _ = quiet(DCF.run_epv, dict(epv, growth=growth))
            with self.subTest(growth=growth):
                self.assertAlmostEqual(100., result["epv_ps"])
                self.assertIsNone(result["growth_ps"])

    def test_sub_cost_incremental_returns_reduce_value(self):
        result = DCF.franchise_growth_value(10., .1, .02, .05)
        self.assertAlmostEqual(75., result["equity"])

    def test_growth_at_cost_of_capital_adds_no_value(self):
        result = DCF.franchise_growth_value(10., .1, .02, .1)
        self.assertAlmostEqual(100., result["equity"])


class ReverseAndRolesTests(unittest.TestCase):
    def config(self):
        return {"wacc": .1, "terminal_g": 0., "shares": 1., "price": 80.,
                "scenarios": [{"name": "base", "fcf": [10.], "prob": 1.}]}

    def test_reverse_cagr_targets_first_terminal_year(self):
        # Price gives terminal-year revenue 121 at a 10% cash margin, two years after revenue 100.
        price = (12.1 / .1) / 1.1
        result = DCF.reverse_dcf(price, 1., 0., .1, 0., [0.], [.1], 100.)
        self.assertEqual(2, result["required_year"])
        self.assertAlmostEqual(121., result["rows"][0]["revenue_required"])
        self.assertAlmostEqual(.1, result["rows"][0]["implied_cagr"])

    def test_negative_required_terminal_revenue_has_no_real_cagr(self):
        result = DCF.reverse_dcf(1., 1., 0., .1, 0., [100.], [.1], 100.)
        self.assertLess(result["rows"][0]["revenue_required"], 0)
        self.assertIsNone(result["rows"][0]["implied_cagr"])

    def test_conditional_upside_never_changes_weighted_value_or_position(self):
        cfg = self.config()
        _, baseline = quiet(DCF.run, cfg)
        cfg["scenarios"].append({"name": "recovery", "fcf": [1000.],
                                 "role": "conditional", "prob": .9})
        result, output = quiet(DCF.run, cfg)
        self.assertAlmostEqual(10000., result["recovery"]["per_share"])
        self.assertIn("conditional 条件情景", output)
        self.assertIn("概率加权公允价值: 100.0/股", output)
        self.assertEqual(baseline.split("=== 仓位思维 ===")[1], output.split("=== 仓位思维 ===")[1])

    def test_conditional_only_produces_no_decision_value(self):
        cfg = self.config()
        cfg["scenarios"][0]["role"] = "conditional"
        cfg["scenarios"][0].pop("prob")
        _, output = quiet(DCF.run, cfg)
        self.assertNotIn("概率加权公允价值:", output)
        self.assertNotIn("=== 仓位思维 ===", output)

    def test_decision_probabilities_must_sum_to_one(self):
        cfg = self.config()
        cfg["scenarios"][0]["prob"] = .5
        cfg["scenarios"].append({"name": "recovery", "fcf": [100.], "role": "conditional", "prob": .5})
        with self.assertRaisesRegex(ValueError, "决策情景概率和"):
            quiet(DCF.run, cfg)

    def test_scalar_conditionals_are_labelled_and_role_is_returned(self):
        cases = [(DCF.run_epv, {"normalized_earnings": 10., "coc": .1, "shares": 1.}, ()),
                 (DCF.run_eva, {"invested_capital": 100., "nopat": 20., "wacc": .1}, ({},)),
                 (DCF.run_montecarlo, dict(DCF.DEMO['montecarlo'], n=2), (DCF.DEMO,))]
        for fn, config, rest in cases:
            result, output = quiet(fn, dict(config, role='conditional'), *rest)
            with self.subTest(method=fn.__name__):
                self.assertEqual('conditional', result['role'])
                self.assertIn('conditional 条件测算（不作为买入基准）', output)
                with self.assertRaises(ValueError):
                    quiet(fn, dict(config, role='unknown'), *rest)


class ValidationTests(unittest.TestCase):
    def test_selected_empty_method_block_reports_missing_inputs(self):
        for key in ('epv', 'eva', 'pvgo', 'montecarlo', 'reverse'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                quiet(DCF.run, {key: {}})

    def test_invalid_paths_years_and_dilution_are_explicit_errors(self):
        scenarios = [{"fcf": []}, {"revenue": [], "fcf_margin": []},
                     {"revenue": [1.], "fcf_margin": [1., 2.]},
                     {"fcf": [math.nan]}, {"fcf": [math.inf]}, {"fcf": [True]},
                     {"fcf": [1.], "fade_years": -1}, {"fcf": [1.], "fade_years": 1.5},
                     {"fcf": [1.], "annual_dilution": -1.},
                     {"fcf": [1.], "annual_dilution": math.inf}]
        for sc in scenarios:
            with self.subTest(sc=sc), self.assertRaises(ValueError):
                DCF.dcf_value(sc, .1, 0., 1., 0.)

    def test_invalid_montecarlo_bounds_not_silently_adjusted(self):
        cfg = copy.deepcopy(DCF.DEMO)
        for overrides in ({"n": 0}, {"years": 0}, {"fade_years": -1},
                          {"wacc_low": .01}, {"margin_mode": 2.}, {"growth_std": math.nan}):
            c = dict(cfg["montecarlo"], **overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                quiet(DCF.run_montecarlo, c, cfg)

    def test_python_optimized_mode_keeps_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "invalid.json")
            with open(path, "w") as handle:
                json.dump({"epv": {"normalized_earnings": 60., "coc": .1, "shares": 0.}}, handle)
            proc = subprocess.run([sys.executable, "-O", SCRIPT, "--config", path],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("epv.shares 必须为正", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
