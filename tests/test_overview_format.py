"""Offline tests for the overview template rules in overview_agent/overview_format.py."""
from __future__ import annotations

import unittest

from overview_agent.overview_format import validate_overview
from tests.test_sandbox import GOOD


class ValidateOverviewTests(unittest.TestCase):
    def test_good_overview_passes_and_each_cited_path_is_checked_once(self):
        checked = []

        def no_problem(token):
            checked.append(token)
            return None

        self.assertEqual(validate_overview(GOOD, no_problem), ([], []))
        self.assertEqual(checked, ["README.md", "src/orders/"])

    def test_callback_results_become_warnings(self):
        errors, warnings = validate_overview(GOOD, lambda token: "missing: %s" % token)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["missing: README.md", "missing: src/orders/"])

    def test_wrong_headings_stop_before_any_path_check(self):
        def must_not_run(token):
            raise AssertionError("cited paths must not be checked when the headings are wrong")

        errors, _ = validate_overview(GOOD.replace("## Open questions", "## Questions"), must_not_run)
        self.assertEqual(len(errors), 1)
        self.assertIn("headings must be exactly", errors[0])


if __name__ == "__main__":
    unittest.main()
