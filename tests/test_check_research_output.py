import importlib.util
import os
import tempfile
import unittest


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


class IntegratedCheckerTests(unittest.TestCase):
    def test_demo_report_passes_report_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _, _ = CHECKER.write_demo_files(tmp)
            issues = []
            CHECKER.check_report(report, None, issues, ["saas"], "zh")
            self.assertEqual([], issues)


if __name__ == "__main__":
    unittest.main()
