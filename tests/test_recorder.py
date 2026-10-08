"""Offline tests for recorder.py: the tool-call counts and run report both engines share."""
from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from overview_agent.recorder import USAGE_KEYS, ToolRecorder, finish_run
from overview_agent.sandbox import RepoSandbox
from tests.test_sandbox import GOOD


class ToolRecorderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "README.md").write_text("# Acme\nBooks appointments.\n")
        (self.root / ".env").write_text("SECRET=1\n")
        self.rec = ToolRecorder(RepoSandbox(str(self.root), log=lambda m: None), log=lambda m: None)

    def test_a_good_call_is_counted_once(self):
        out, is_error = self.rec.call("read_file", {"path": "README.md", "tier": 1})
        self.assertFalse(is_error)
        self.assertIn("Books appointments", out)
        self.assertEqual((self.rec.counts["tool_calls"], self.rec.counts["tool_errors"]), (1, 0))

    def test_duplicates_ignore_argument_order_and_count_only_repeats(self):
        self.rec.call("list_tree", {"path": ".", "max_depth": 2})
        self.rec.call("list_tree", {"max_depth": 2, "path": "."})
        self.rec.call("list_tree", {"path": ".", "max_depth": 1})
        self.assertEqual(self.rec.counts["duplicate_calls"], 1)
        self.assertEqual(self.rec.counts["tool_calls"], 3)

    def test_a_sandbox_rejection_is_an_error_and_a_rejection(self):
        out, is_error = self.rec.call("read_file", {"path": ".env", "tier": 1})
        self.assertTrue(is_error)
        self.assertEqual((self.rec.counts["tool_errors"], self.rec.counts["rejected_calls"]), (1, 1))

    def test_a_tool_bug_is_an_error_but_not_a_rejection(self):
        self.rec.sandbox.call = lambda name, args: 1 / 0
        out, is_error = self.rec.call("list_tree", {})
        self.assertTrue(is_error)
        self.assertTrue(out.startswith("internal tool error:"))
        self.assertEqual((self.rec.counts["tool_errors"], self.rec.counts["rejected_calls"]), (1, 0))

    def test_first_write_ok_reflects_only_the_first_write(self):
        self.rec.call("write_overview", {"content": "# nope"})
        self.rec.call("write_overview", {"content": GOOD})
        self.assertEqual((self.rec.counts["write_attempts"], self.rec.counts["first_write_ok"]), (2, False))
        self.assertTrue(self.rec.sandbox.overview_written)


class FinishRunTests(unittest.TestCase):
    def run_finish(self, written: bool, error: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = RepoSandbox(tmp, log=lambda m: None)
            sandbox.overview_written = written
            rec = ToolRecorder(sandbox, log=lambda m: None)
            usage = dict(zip(USAGE_KEYS, (1, 2, 3, 4)))
            path = Path(tmp) / "m.json"
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = finish_run("api", "claude-sonnet-5-5", sandbox, rec, usage, 3, time.monotonic(), "done",
                                  str(path), extra={"denied_calls": 0}, error=error)
            return code, json.loads(path.read_text()), err.getvalue()

    def test_metrics_carry_engine_counts_usage_and_extras(self):
        code, metrics, _ = self.run_finish(written=True)
        self.assertEqual(code, 0)
        self.assertEqual(metrics["engine"], "api")
        self.assertEqual(metrics["turns"], 3)
        self.assertEqual([metrics[k] for k in USAGE_KEYS], [1, 2, 3, 4])
        for key in ("tool_calls", "duplicate_calls", "rejected_calls", "first_write_ok", "denied_calls",
                    "model", "wall_seconds", "tier3_files", "overview_written"):
            self.assertIn(key, metrics)

    def test_not_written_exits_1(self):
        code, _, err = self.run_finish(written=False)
        self.assertEqual(code, 1)
        self.assertIn("was not written", err)

    def test_an_error_exits_1_and_still_writes_metrics(self):
        code, metrics, err = self.run_finish(written=True, error="the Agent SDK run failed")
        self.assertEqual(code, 1)
        self.assertIn("error: the Agent SDK run failed", err)
        self.assertTrue(metrics["overview_written"])


if __name__ == "__main__":
    unittest.main()
