"""Malformed evidence inputs must fail review without exceptions or false passes."""

import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from research_review import review_issues


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


if __name__ == "__main__":
    unittest.main()
