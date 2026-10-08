"""Offline tests for bench.py (a fake runner and judge: no processes, no API, no E2B)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from overview_agent import bench
from overview_agent.overview_format import OVERVIEW_NAME

USAGE = {"input_tokens": 100, "output_tokens": 1000, "cache_creation_input_tokens": 10000,
         "cache_read_input_tokens": 100000}  # $0.0002 + $0.01 + $0.025 + $0.02 = $0.0552 per run
EXPECTED = ["local/api/run-1", "e2b/api/run-1", "e2b/api/run-2", "e2b/api/run-3",
            "local/agent-sdk/run-1", "e2b/agent-sdk/run-1", "e2b/agent-sdk/run-2", "e2b/agent-sdk/run-3"]


class FakeRunner:
    def __init__(self, fail_at=None, usage=USAGE):
        self.calls, self.fail_at, self.usage, self.saw_env_file = [], fail_at, usage, []

    def __call__(self, argv, run_dir):
        self.calls.append(argv)
        engine = argv[argv.index("--engine") + 1]
        (run_dir / "metrics.json").write_text(json.dumps(dict(self.usage, engine=engine, turns=3, wall_seconds=9.5)))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            return 1
        if argv[2] == "local":
            self.saw_env_file.append(Path(argv[3], ".env").exists())
            Path(argv[3], OVERVIEW_NAME).write_text("# by %s\n" % engine)
        else:
            (run_dir / OVERVIEW_NAME).write_text("# by %s\n" % engine)
        return 0


def fake_judge(overview):
    value = 5 if "agent-sdk" in overview else 3
    return {"model": "claude-sonnet-5-5", "total": value * 4, "usage": dict(USAGE),
            "scores": {c: {"score": value, "reason": "r"} for c in bench.CRITERIA}}


class PureTests(unittest.TestCase):
    def test_each_engine_runs_back_to_back_local_first(self):
        self.assertEqual([r.rel for r in bench.plan_runs()], EXPECTED)

    def test_cost_uses_list_prices_per_million(self):
        one_million = dict.fromkeys(USAGE, 1_000_000)
        self.assertAlmostEqual(bench.cost_usd(one_million, "claude-sonnet-5-5"), 14.70)

    def test_spread_is_median_and_range(self):
        self.assertEqual(bench.spread([3, 1, 2], "%d"), "2 [1–3]")
        self.assertEqual(bench.spread([4], "%d"), "4")
        self.assertEqual(bench.spread([None], "%d"), "—")


class RunBenchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "terra"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("# Terra\n")
        (self.repo / ".env").write_text("SECRET=1\n")
        (self.repo / OVERVIEW_NAME).write_text("untracked original\n")
        self.out = Path(self._tmp.name) / "out"

    def bench(self, runner, model="claude-sonnet-5-5", max_usd=3.0):
        args = SimpleNamespace(model=model, max_turns=25, out=str(self.out), max_usd=max_usd)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = bench.run_bench(str(self.repo), args, runner=runner, judge_fn=fake_judge)
        return code, err.getvalue()

    def test_full_bench_runs_8_judges_8_and_writes_the_summary(self):
        runner = FakeRunner()
        code, _ = self.bench(runner)
        self.assertEqual(code, 0)
        self.assertEqual(len(runner.calls), 8)
        for rel in EXPECTED:
            self.assertTrue((self.out / rel / "judge.json").is_file(), rel)
            self.assertEqual(json.loads((self.out / rel / "bench.json").read_text())["exit_code"], 0)
        summary = (self.out / "summary.md").read_text()
        self.assertIn("| Metric | local · api | local · agent-sdk | E2B · api | E2B · agent-sdk |", summary)
        self.assertIn("| Turns | 3 | 3 | 3 [3–3] | 3 [3–3] |", summary)
        self.assertIn("## Spot check", summary)

    def test_local_runs_use_a_filtered_copy_and_never_touch_the_source(self):
        runner = FakeRunner()
        self.bench(runner)
        local_repos = [argv[3] for argv in runner.calls if argv[2] == "local"]
        self.assertEqual(len(local_repos), 2)
        for path in local_repos:
            self.assertNotEqual(Path(path).resolve(), self.repo.resolve())
        self.assertEqual(runner.saw_env_file, [False, False])
        self.assertEqual((self.repo / OVERVIEW_NAME).read_text(), "untracked original\n")
        remote_sources = [argv[3] for argv in runner.calls if argv[2] == "remote"]
        self.assertEqual(set(remote_sources), {str(self.repo.resolve())})  # macOS: /var is /private/var

    def test_a_failed_run_stops_the_bench_and_resume_reruns_it(self):
        code, err = self.bench(FakeRunner(fail_at=3))
        self.assertEqual(code, 1)
        self.assertIn("e2b/api/run-2 failed", err)
        resumed = FakeRunner()
        code, _ = self.bench(resumed)
        self.assertEqual(code, 0)
        self.assertEqual(len(resumed.calls), 6)  # e2b/api/run-2 onward
        self.assertEqual(len(list((self.out / "e2b" / "api").glob("run-2.failed-*"))), 1)

    def test_the_spend_guard_stops_before_passing_max_usd(self):
        expensive = dict.fromkeys(USAGE, 0)
        expensive["output_tokens"] = 100_000  # $1.00 per run
        runner = FakeRunner(usage=expensive)
        code, err = self.bench(runner, max_usd=2.5)
        self.assertEqual(code, 1)
        self.assertEqual(len(runner.calls), 2)  # $2.00 spent + $1.00 next > $2.50
        self.assertIn("--max-usd", err)

    def test_an_unpriced_model_fails_before_any_run(self):
        runner = FakeRunner()
        code, err = self.bench(runner, model="claude-unknown")
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [])
        self.assertIn("no prices for model claude-unknown", err)


if __name__ == "__main__":
    unittest.main()
