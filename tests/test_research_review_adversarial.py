"""Malformed evidence inputs must fail review without exceptions or false passes."""

import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from research_review import review_issues
from test_research_review import growth_example, direct_fcf_example, mc_example


class ResearchReviewAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "references" / "research-review-example.json").read_text())

    def assert_blocked(self, cfg):
        issues = review_issues(cfg, require_review=True)
        self.assertTrue(any(issue["severity"] in {"P0", "P1"} for issue in issues), issues)
        return issues

    def test_malformed_nested_containers_fail_without_crashing(self):
        mutations = [
            (("sources",), {}),
            (("sources", 0), "source"),
            (("baseline",), []),
            (("baseline", "periods"), "last two quarters"),
            (("baseline", "periods", 0), None),
            (("claims",), {}),
            (("claims", 0), "recovery"),
            (("claims", 0, "benchmark"), []),
            (("claims", 0, "evidence"), []),
            (("claims", 0, "evidence", "business"), ["strong"]),
            (("earnings_bridge",), []),
            (("earnings_bridge", "adjustments"), {}),
            (("decision", "model_paths"), [None]),
        ]
        for path, malformed in mutations:
            with self.subTest(path=path):
                cfg = copy.deepcopy(self.cfg)
                node = cfg["research_review"]
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = malformed
                self.assert_blocked(cfg)

    def test_renaming_source_does_not_leave_evidence_valid(self):
        self.cfg["research_review"]["sources"][0]["id"] = "unreferenced-source"
        issues = self.assert_blocked(self.cfg)
        self.assertTrue(any(issue["code"] == "REVIEW_SOURCE_UNLINKED" for issue in issues))

    def test_boolean_version_is_not_schema_version_one(self):
        self.cfg["research_review"]["version"] = True
        issues = self.assert_blocked(self.cfg)
        self.assertTrue(any(issue["code"] == "RESEARCH_REVIEW_VERSION" for issue in issues))

    def test_nonfinite_values_cannot_satisfy_bridge_or_model_matching(self):
        for nonfinite in (float("inf"), float("-inf"), float("nan")):
            for field in ("target_margin", "current_earnings"):
                with self.subTest(nonfinite=nonfinite, field=field):
                    cfg = copy.deepcopy(self.cfg)
                    cfg["research_review"]["earnings_bridge"][field] = nonfinite
                    self.assert_blocked(cfg)

    def test_valid_pointer_cannot_substitute_for_required_parameter(self):
        claim = self.cfg["research_review"]["claims"][0]
        claim.update(parameter="/epv/shares", assumed_value=10, conservative_value=10)
        issues = self.assert_blocked(self.cfg)
        self.assertTrue(any(issue["code"] == "REVIEW_KEY_ASSUMPTION_MISSING" for issue in issues))

    def test_cash_baseline_and_operating_bridge_reject_malformed_fields(self):
        for value in (None, [], True, float('inf'), float('nan')):
            with self.subTest(value=value):
                cfg = growth_example()
                cfg['research_review']['baseline']['cash_flow']['fcff'] = value
                self.assert_blocked(cfg)
                cfg = direct_fcf_example()
                cfg['scenarios'][0]['operating_bridge']['da'] = value
                self.assert_blocked(cfg)
        cfg = direct_fcf_example()
        cfg['scenarios'][0]['operating_bridge']['capex'].pop()
        self.assert_blocked(cfg)
        cfg = growth_example()
        cfg['research_review']['baseline']['cash_flow'].update(revenue=1e-300, nopat=1e300, fcff=1e300)
        cfg['research_review']['baseline']['annual_earnings'] = 1e300
        self.assert_blocked(cfg)
        cfg = direct_fcf_example()
        cfg['scenarios'][0]['fcf'][0] = 1e300
        cfg['scenarios'][0]['operating_bridge']['revenue'][0] = 5e-324
        cfg['scenarios'][0]['operating_bridge']['da'][0] = 1e300
        issues = self.assert_blocked(cfg)
        self.assertTrue(any(i['code'] == 'REVIEW_FCFF_BRIDGE_INVALID' for i in issues))

    def test_distribution_records_reject_incomplete_or_false_snapshots(self):
        for field, value in [('assumed_spec', {}), ('assumed_summary', None),
                             ('conservative_spec', []), ('conservative_summary', {}),
                             ('benchmark', []), ('evidence', []), ('use', 'conditional')]:
            with self.subTest(field=field):
                cfg = mc_example()
                cfg['research_review']['montecarlo_distribution'][field] = value
                self.assert_blocked(cfg)
        for value in (None, [], True):
            cfg = mc_example()
            cfg['research_review']['montecarlo_distribution'] = value
            self.assert_blocked(cfg)

    def test_distribution_family_and_upper_tail_cannot_be_silently_rewritten(self):
        cfg = mc_example()
        cfg['research_review']['montecarlo_distribution']['assumed_spec']['growth_distribution'] = 'truncated_normal'
        self.assert_blocked(cfg)
        cfg = mc_example()
        cfg['research_review']['montecarlo_distribution']['assumed_summary']['margin_p90'] = float('nan')
        self.assert_blocked(cfg)


if __name__ == "__main__":
    unittest.main()
