import importlib.util
import argparse
import contextlib
import io
import json
import os
import tempfile
import types
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(__file__))
SCRIPT = os.path.join(ROOT, "scripts", "check_research_output.py")
SPEC = importlib.util.spec_from_file_location("check_research_output", SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class IndustryRulesTests(unittest.TestCase):
    def test_registry_has_valid_appendices_and_kpi_groups(self):
        rules = CHECKER.load_industry_rules()
        self.assertEqual(20, len(rules))
        for slug, rule in rules.items():
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rule["appendix"])), slug)
            self.assertGreaterEqual(len(rule["required_groups"]), 2, slug)
            for group in rule["required_groups"]:
                self.assertTrue(group["terms"], f"{slug}: {group['label']}")

    def test_auto_detects_primary_and_secondary_appendices(self):
        rules = CHECKER.load_industry_rules()
        text = "Industry appendix: Internet/platforms (primary) + SaaS (secondary)"
        self.assertEqual(["saas", "internet-platform"], CHECKER.detect_declared_industries(text, rules))

    def test_platform_equivalent_kpis_pass(self):
        issues = []
        text = "行业附录: internet-platform\n| 广告变现 | 分部经营利润率 |\n|---:|---:|\n| +10% | 20% |"
        CHECKER.check_industry_requirements(text, ["auto"], "report.md", issues)
        self.assertEqual([], issues)

    def test_missing_industry_kpi_is_p1(self):
        issues = []
        CHECKER.check_industry_requirements("行业附录: banks\n| NIM |\n|---:|\n| 3% |", ["auto"], "report.md", issues)
        self.assertTrue(any(issue.code == "REPORT_INDUSTRY_KPI_MISSING" and issue.severity == "P1" for issue in issues))


class LanguageTests(unittest.TestCase):
    def test_english_report_rejects_chinese_template_marker(self):
        issues = []
        CHECKER.check_language_consistency("# Acme (ACME) Equity Research Report\n本章要点：增长。", "auto", "report.md", issues)
        self.assertEqual("REPORT_LANGUAGE_MIXED", issues[0].code)

    def test_chinese_report_accepts_chinese_template_markers(self):
        issues = []
        CHECKER.check_language_consistency("# 示例（DEMO）个股投资研究报告\n本章要点：增长。", "auto", "report.md", issues)
        self.assertEqual([], issues)


class FinancialHeaderTests(unittest.TestCase):
    def check_csv(self, headers, values):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "financials.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(headers + "\n" + values + "\n")
            issues = []
            CHECKER.check_financials(path, issues)
            return issues, CHECKER.load_csv(path)[1]

    def test_chinese_and_english_headers_produce_same_valid_result(self):
        values = "2025,100,60,60%,30,10,20,20%"
        for headers in (
            "period,revenue,gross_profit,gross_margin,cfo,capex,fcf,fcf_margin",
            "期间,收入,毛利,毛利率,经营现金流,资本开支,自由现金流,自由现金流率",
            "期间,Ｒｅｖｅｎｕｅ,毛利,gross margin,经营现金流,capex,自由现金流,fcf_margin",
        ):
            with self.subTest(headers=headers):
                issues, _ = self.check_csv(headers, values)
                self.assertEqual([], issues)

    def test_chinese_missing_columns_are_not_read_from_another_column(self):
        issues, aliases = self.check_csv("期间,收入,毛利,毛利率", "2025,100,60,60%")
        self.assertEqual([], issues)
        self.assertIsNone(CHECKER.pick(aliases, ["net_income", "净利润"]))
        self.assertEqual("收入", CHECKER.pick(aliases, ["revenue", "收入"]))

    def test_chinese_ratio_error_is_still_detected(self):
        issues, _ = self.check_csv("期间,收入,毛利,毛利率", "2025,100,60,40%")
        self.assertEqual(["GROSS_MARGIN_MISMATCH"], [issue.code for issue in issues])

    def test_scientific_notation_preserves_tiny_cash_flow_residuals(self):
        issues, _ = self.check_csv("period,revenue,cfo,capex,fcf,fcf_margin",
                                   "2024Q4,440,10.8,10.8,1.7763568394002505e-15,4.037174635000569e-18")
        self.assertEqual([], issues)
        for value, expected in (("-1.5e-3", -.0015), ("$1.2E+3", 1200), ("1e1%", .1), (".5", .5)):
            with self.subTest(value=value):
                self.assertAlmostEqual(expected, CHECKER.parse_number(value))

    def test_duplicate_and_normalized_collisions_are_rejected(self):
        for duplicate in ("revenue,revenue", "Revenue,ＲＥＶＥＮＵＥ", "收入,收 入"):
            with self.subTest(duplicate=duplicate):
                issues, aliases = self.check_csv("period," + duplicate + ",gross_profit,gross_margin", "2025,100,200,60,60%")
                self.assertTrue(any(issue.code == "FINANCIALS_AMBIGUOUS_HEADER" and issue.severity == "P1" for issue in issues))
                self.assertIsNone(CHECKER.pick(aliases, ["revenue", "收入"]))
                self.assertFalse(any(issue.code == "GROSS_MARGIN_MISMATCH" for issue in issues))

    def test_empty_header_is_reported_without_using_it(self):
        issues, aliases = self.check_csv("期间,收入,---", "2025,100,60")
        self.assertEqual(["FINANCIALS_EMPTY_HEADER"], [issue.code for issue in issues])
        self.assertNotIn("", aliases)


class FinancialPeriodTests(unittest.TestCase):
    def check_csv(self, data, industries=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "financials.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(data)
            issues = []
            CHECKER.check_financials(path, issues, industries)
            return issues

    def test_annual_yoy_uses_previous_year_and_checks_short_histories(self):
        data = "period,revenue,revenue_yoy\n2021,100,\n2022,110,10%\n2023,121,10%\n2024,133.1,10%\n2025,146.41,10%\n"
        self.assertEqual([], self.check_csv(data))
        wrong = "period,revenue,revenue_yoy\n2023,100,\n2024,110,100%\n2025,121,100%\n"
        self.assertEqual(["REVENUE_YOY_MISMATCH"] * 2, [issue.code for issue in self.check_csv(wrong)])

    def test_unsorted_mixed_frequencies_match_only_their_own_periods(self):
        data = "period,revenue,revenue_yoy\n2025Q2,300,50%\nFY2025,110,10%\n2024Q2,200,\n2024,100,\n2025H2,240,20%\n2024H2,200,\n"
        self.assertEqual([], self.check_csv(data))

    def test_quarter_qoq_handles_year_boundary_and_missing_periods(self):
        valid = "period,revenue,revenue_qoq\n2025Q1,110,10%\n2024Q4,100,\n"
        self.assertEqual([], self.check_csv(valid))
        missing = valid.replace("2024Q4", "2024Q3")
        issues = self.check_csv(missing)
        self.assertEqual(["FINANCIALS_GROWTH_UNCHECKED"], [issue.code for issue in issues])

    def test_unknown_periods_and_annual_qoq_are_not_guessed(self):
        for label in ("Q1", "2025-03-31", "TTM", "2025"):
            with self.subTest(label=label):
                issues = self.check_csv(f"period,revenue,revenue_qoq\n{label},100,10%\n")
                self.assertEqual(["FINANCIALS_GROWTH_UNCHECKED"], [issue.code for issue in issues])

    def test_duplicate_period_aliases_are_rejected(self):
        issues = self.check_csv("period,revenue,revenue_yoy\n2024,100,\n2025,110,10%\nFY2025,120,20%\n")
        self.assertTrue(any(issue.code == "FINANCIALS_DUPLICATE_PERIOD" and issue.severity == "P1" for issue in issues))

    @staticmethod
    def forensic_csv(periods, forecast=False):
        data = "period,revenue,receivables,gross_profit,ppe,current_assets,depreciation,sga,total_liabilities,total_assets,net_income,cfo,data_type\n"
        for period, revenue in periods:
            status = "forecast" if forecast and period == "2026" else "actual"
            data += ",".join(map(str, [period, revenue, .2 * revenue, .5 * revenue, 100, 200, 10, .1 * revenue,
                                      200, 500, .1 * revenue, .1 * revenue, status])) + "\n"
        return data

    def test_seasonal_quarters_never_generate_annual_mscore(self):
        data = self.forensic_csv([("2024Q1", 100), ("2024Q2", 200), ("2024Q3", 100),
                                  ("2024Q4", 100), ("2025Q1", 100), ("2025Q2", 200)])
        issues = self.check_csv(data)
        self.assertEqual(["FORENSIC_ANNUAL_DATA_REQUIRED"], [issue.code for issue in issues])

    def test_forensics_sorts_annuals_and_excludes_forecasts_and_quarters(self):
        data = self.forensic_csv([("2025", 100), ("2024", 100), ("2025Q2", 200), ("2026", 1000)], forecast=True)
        issues = self.check_csv(data)
        score = next(issue for issue in issues if issue.code == "FORENSIC_MSCORE_INFO")
        self.assertIn("M=-2.48", score.detail)
        self.assertFalse(any(issue.category == "business_risk" for issue in issues))
        self.assertIn("FORENSIC_FORECAST_EXCLUDED", [issue.code for issue in issues])

    def test_forensics_does_not_bridge_missing_years_or_unknown_periods(self):
        for periods, expected in (([("2023", 100), ("2025", 200)], "FORENSIC_COMPARABLE_YEAR_MISSING"),
                                  ([("Q1", 100), ("Q2", 200)], "FORENSIC_PERIOD_UNCHECKED")):
            with self.subTest(periods=periods):
                issues = self.check_csv(self.forensic_csv(periods))
                self.assertIn(expected, [issue.code for issue in issues])
                self.assertFalse(any(issue.code.startswith("FORENSIC_MSCORE") for issue in issues))

    def test_accruals_use_average_assets(self):
        data = "period,revenue,net_income,cfo,total_assets\n2024,100,10,10,100\n2025,100,40,10,300\n"
        issue = next(issue for issue in self.check_csv(data) if issue.code == "FORENSIC_HIGH_ACCRUALS")
        self.assertIn("15.0%", issue.message)
        self.assertIn("average_total_assets=200.0", issue.detail)


class BusinessRiskTests(unittest.TestCase):
    DATA = "period,revenue,net_income,cfo,total_assets,average_total_assets\n2025,500,100,10,500,500\n"

    def run_check(self, data=None, strict=False, industries=None, report=None, as_json=True):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "financials.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(data or self.DATA)
            report_path = None
            if report is not None:
                report_path = os.path.join(tmp, "report.md")
                with open(report_path, "w", encoding="utf-8") as handle:
                    handle.write(report)
            args = argparse.Namespace(report=report_path, assumptions=None, financials=path, industry=industries,
                                      language="zh", scope="direct", json=as_json, strict=strict)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = CHECKER.run(args)
            return result, json.loads(output.getvalue()) if as_json else output.getvalue()

    def test_risks_remain_visible_without_becoming_validation_failures(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                code, issues = self.run_check(strict=strict)
                self.assertEqual(0, code)
                self.assertEqual([("P1", "business_risk")], [(issue["severity"], issue["category"]) for issue in issues])
                self.assertIn("人工核查", issues[0]["message"])

    def test_text_output_does_not_certify_risk_resolution(self):
        code, output = self.run_check(as_json=False)
        self.assertEqual(0, code)
        self.assertIn("退出码为 0 不表示风险已核查", output)
        self.assertNotIn("检查通过", output)

    def test_real_validation_errors_still_block_with_business_risks(self):
        data = "period,revenue,net_income,cfo,total_assets,average_total_assets,gross_profit,gross_margin\n2025,500,100,10,500,500,100,50%\n"
        for strict in (False, True):
            code, issues = self.run_check(data, strict=strict)
            self.assertEqual(1, code)
            self.assertIn("GROSS_MARGIN_MISMATCH", [issue["code"] for issue in issues])

    def test_financial_industries_are_exempt_but_mixed_groups_are_not(self):
        for industries in (["banks"], ["insurance"], ["banks", "insurance"]):
            with self.subTest(industries=industries):
                code, issues = self.run_check(industries=industries, strict=True)
                self.assertEqual(0, code)
                self.assertEqual(["FORENSIC_SECTOR_EXEMPT"], [issue["code"] for issue in issues])
        _, mixed = self.run_check(industries=["banks", "saas"])
        self.assertIn("FORENSIC_MIXED_INDUSTRIES", [issue["code"] for issue in mixed])
        self.assertIn("FORENSIC_HIGH_ACCRUALS", [issue["code"] for issue in mixed])

    def test_report_industry_auto_and_secondary_business_are_respected(self):
        code, issues = self.run_check(industries=["auto"], report="行业附录: banks", strict=True)
        self.assertEqual(0, code)
        self.assertEqual(["FORENSIC_SECTOR_EXEMPT"], [issue["code"] for issue in issues])
        _, mixed = self.run_check(industries=["banks"], report="行业附录: saas")
        self.assertIn("FORENSIC_HIGH_ACCRUALS", [issue["code"] for issue in mixed])


class ValuationAssumptionTests(unittest.TestCase):
    def check_config(self, cfg):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "valuation.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle)
            issues = []
            review = types.SimpleNamespace(review_issues=lambda cfg, require_review=False: [])
            with mock.patch.dict("sys.modules", {"research_review": review}):
                CHECKER.check_assumptions(path, issues)
            return issues

    @staticmethod
    def scenario_config():
        return {
            "shares": 10, "wacc": 0.09, "terminal_g": 0.02,
            "scenarios": [{"name": "base", "prob": 1, "fcf": [10, 11], "probability_rationale": "Current contracted demand supports this case."}],
        }

    def test_epv_only_needs_no_dcf_fields(self):
        issues = self.check_config({"epv": {"earnings_basis": "NI", "normalized_earnings": 60, "coc": 0.12, "shares": 10}})
        self.assertEqual([], issues)

    def test_model_numeric_inputs_reject_strings_booleans_and_nonfinite_values(self):
        for invalid in ("9%", "0.09", "1,000", True, False, float("nan"), float("inf"), float("-inf")):
            with self.subTest(invalid=invalid):
                cfg = {"epv": {"earnings_basis": "NI", "normalized_earnings": 60, "coc": invalid, "shares": 10}}
                issues = self.check_config(cfg)
                self.assertTrue(any(issue.code == "ASSUMPTION_INVALID_NUMBER" and "epv.coc" in issue.message for issue in issues))

    def test_model_vector_and_top_level_numbers_are_not_coerced(self):
        for field in ("fcf", "prob", "wacc", "shares"):
            with self.subTest(field=field):
                cfg = self.scenario_config()
                if field == "fcf":
                    cfg["scenarios"][0]["fcf"] = [10, "11"]
                elif field == "prob":
                    cfg["scenarios"][0]["prob"] = "1"
                else:
                    cfg[field] = str(cfg[field])
                issues = self.check_config(cfg)
                self.assertTrue(any(issue.code == "ASSUMPTION_INVALID_NUMBER" and field in issue.message for issue in issues))

    def test_explicit_empty_epv_is_not_silently_ignored(self):
        issues = self.check_config({"epv": {}})
        self.assertTrue(any(issue.code == "ASSUMPTION_INVALID_NUMBER" and "normalized_earnings" in issue.message for issue in issues))

    def test_invalid_model_shapes_report_errors_without_crashing(self):
        for cfg in ({"epv": []}, {"scenarios": {}}, {"scenarios": ["base"]}):
            with self.subTest(cfg=cfg):
                self.assertEqual(["ASSUMPTION_MODEL_INVALID"], [issue.code for issue in self.check_config(cfg)])

    def test_equity_epv_cannot_declare_fcff(self):
        issues = self.check_config({"epv": {"earnings_basis": "NI", "cashflow_basis": "FCFF", "normalized_earnings": 60, "coc": 0.12, "shares": 10}})
        self.assertTrue(any(issue.code == "VALUATION_CASHFLOW_BASIS_MISMATCH" for issue in issues))

    def test_dcf_only_needs_no_epv(self):
        self.assertEqual([], self.check_config(self.scenario_config()))

    def test_distinct_firm_and_equity_rates_are_valid(self):
        cfg = self.scenario_config()
        cfg["epv"] = {"earnings_basis": "NET_INCOME", "normalized_earnings": 60, "coc": 0.12, "shares": 10, "discount_rate_basis": "COE"}
        self.assertEqual([], self.check_config(cfg))

    def test_wrong_equity_rate_basis_is_rejected(self):
        issues = self.check_config({"epv": {"earnings_basis": "NI", "normalized_earnings": 60, "coc": 0.12, "shares": 10, "discount_rate_basis": "WACC"}})
        self.assertTrue(any(issue.code == "VALUATION_RATE_BASIS_MISMATCH" for issue in issues))

    def test_pvgo_requires_explicit_equity_rate(self):
        issues = self.check_config({"wacc": 0.09, "pvgo": {"earnings_ps": 6}})
        self.assertTrue(any(issue.code == "ASSUMPTION_INVALID_NUMBER" and "pvgo.r" in issue.message for issue in issues))

    def test_net_cash_is_not_added_again(self):
        cfg = self.scenario_config()
        cfg["net_debt"], cfg["excess_cash"] = -50, 50
        self.assertTrue(any(issue.code == "VALUATION_CASH_DOUBLE_COUNT" for issue in self.check_config(cfg)))

    def test_net_income_epv_cannot_deduct_debt_twice(self):
        cfg = {"epv": {"earnings_basis": "NI", "normalized_earnings": 60, "coc": 0.12, "shares": 10, "net_debt": 20}}
        self.assertTrue(any(issue.code == "EPV_EQUITY_DEBT_DOUBLE_COUNT" for issue in self.check_config(cfg)))

    def test_conditional_probability_is_optional_and_excluded(self):
        for probability in (None, 0.9):
            cfg = self.scenario_config()
            case = {"name": "recovery", "role": "conditional", "fcf": [20, 30]}
            if probability is not None:
                case["prob"] = probability
            cfg["scenarios"].append(case)
            self.assertEqual([], self.check_config(cfg))

    def test_conditional_scenario_still_requires_valid_numbers(self):
        cfg = self.scenario_config()
        cfg["scenarios"].append({"name": "recovery", "role": "conditional", "fcf": []})
        self.assertTrue(any(issue.code == "DCF_SCENARIO_NO_FCF_OR_DRIVERS" for issue in self.check_config(cfg)))

    def test_strong_evidence_does_not_impose_probability_floor(self):
        cfg = self.scenario_config()
        cfg["scenarios"][0]["prob"] = 0.9
        cfg["scenarios"].append({"name": "bull", "prob": 0.1, "fcf": [20, 30], "evidence_strength": "strong", "probability_rationale": "Strong evidence covers only one necessary condition.", "evidence_update": "Raised from 5% after signed customer contract."})
        self.assertEqual([], self.check_config(cfg))

    def test_probability_diagnostics_require_explanation_not_strength_label(self):
        cfg = self.scenario_config()
        case = cfg["scenarios"][0]
        case.pop("probability_rationale")
        case["evidence_strength"] = "strong"
        self.assertEqual({"DCF_PROBABILITY_RATIONALE_MISSING", "DCF_EVIDENCE_UPDATE_MISSING"}, {issue.code for issue in self.check_config(cfg)})


class ScopeTests(unittest.TestCase):
    def test_direct_reply_does_not_require_report_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reply.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("这里的资本成本指权益成本。")
            issues = []
            CHECKER.check_report(path, None, issues, scope="direct")
            self.assertEqual([], issues)

    def test_focused_analysis_does_not_require_unused_methods_or_industry_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "focused.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("我的判断：本次利润率变化源于产品组合。来源：[公司披露](https://example.com/results)，2026-10-05。")
            issues = []
            CHECKER.check_report(path, None, issues, scope="focused")
            self.assertEqual([], issues)

    def test_every_scope_enforces_review_for_reports_with_valuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, assumptions, _ = CHECKER.write_demo_files(tmp)
            review_fn = mock.Mock(return_value=[{"severity": "P1", "code": "TEST_REVIEW_BLOCKED", "message": "unsupported recovery"}])
            review = types.SimpleNamespace(review_issues=review_fn)
            for scope in ("direct", "focused", "deep"):
                args = argparse.Namespace(report=report, assumptions=assumptions, financials=None, industry=["saas"], language="zh", scope=scope, json=True, strict=False)
                with self.subTest(scope=scope), mock.patch.dict("sys.modules", {"research_review": review}), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(1, CHECKER.run(args))
                    self.assertIn("TEST_REVIEW_BLOCKED", output.getvalue())
                    self.assertTrue(review_fn.call_args.kwargs["require_review"])


class IntegratedCheckerTests(unittest.TestCase):
    def test_demo_report_passes_report_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _, _ = CHECKER.write_demo_files(tmp)
            issues = []
            CHECKER.check_report(report, None, issues, ["saas"], "zh")
            self.assertEqual([], issues)

    def test_demo_passes_real_review_and_all_checks_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, assumptions, financials = CHECKER.write_demo_files(tmp)
            args = argparse.Namespace(report=report, assumptions=assumptions, financials=financials, industry=["saas"], language="zh", scope="deep", json=True, strict=True)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, CHECKER.run(args))
            self.assertEqual([], json.loads(output.getvalue()))

    def test_real_earnings_bridge_failure_blocks_every_report_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, assumptions, _ = CHECKER.write_demo_files(tmp)
            cfg = CHECKER.load_json(assumptions)
            cfg["research_review"]["earnings_bridge"]["target_margin"] = 0.20
            with open(assumptions, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle)
            for scope in ("direct", "focused", "deep"):
                args = argparse.Namespace(report=report, assumptions=assumptions, financials=None, industry=["saas"], language="zh", scope=scope, json=True, strict=False)
                with self.subTest(scope=scope), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(1, CHECKER.run(args))
                self.assertIn("EPV_EARNINGS_BRIDGE_MISMATCH", output.getvalue())

    def test_legacy_config_is_explicitly_unreviewed_and_cannot_support_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, assumptions, _ = CHECKER.write_demo_files(tmp)
            cfg = CHECKER.load_json(assumptions)
            cfg.pop("research_review")
            with open(assumptions, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle)
            for require_review, severity in ((False, "P2"), (True, "P1")):
                issues = []
                CHECKER.check_assumptions(assumptions, issues, require_review=require_review)
                self.assertEqual([("RESEARCH_REVIEW_NOT_PERFORMED", severity)], [(issue.code, issue.severity) for issue in issues])


if __name__ == "__main__":
    unittest.main()
