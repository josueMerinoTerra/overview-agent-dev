"""Benchmark both engines on one repo: run the matrix, judge every overview blind, write summary.md.

Run it with `python main.py bench <repo_path>`. Each run is its own `main.py local` or `main.py remote` process, so
the bench measures exactly what a user would run. Local runs get a filtered temp copy of the repo (the same files
an E2B run uploads), so the source folder is never written. Pass an earlier --out to resume.
"""
from __future__ import annotations

import io
import json
import random
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from overview_agent.config import ENGINES, PROJECT_ROOT, trace
from overview_agent.judge import CRITERIA, JUDGE_MODEL, JudgeError
from overview_agent.overview_format import OVERVIEW_NAME

E2B_RUNS = 3
DEFAULT_RUN_ESTIMATE_USD = 0.30  # before any run has finished
# USD per million tokens, list prices (as in docs/specs/conversation-caching.md).
PRICES = {
    "claude-sonnet-5-5": {"input_tokens": 2.00, "output_tokens": 10.00,
                          "cache_creation_input_tokens": 2.50, "cache_read_input_tokens": 0.20},
}
COLUMNS = (("local", "api"), ("local", "agent-sdk"), ("e2b", "api"), ("e2b", "agent-sdk"))
Runner = Callable[[List[str], Path], int]


@dataclass(frozen=True)
class Run:
    env: str     # "local" or "e2b"
    engine: str  # one of ENGINES
    n: int       # 1-based within (env, engine)

    @property
    def rel(self) -> str:
        return "%s/%s/run-%d" % (self.env, self.engine, self.n)


def plan_runs() -> List[Run]:
    """Each engine's runs back to back: the local run first (the pilot and the cold-cache run), then E2B."""
    runs = []
    for engine in ENGINES:
        runs.append(Run("local", engine, 1))
        runs += [Run("e2b", engine, n) for n in range(1, E2B_RUNS + 1)]
    return runs


def cost_usd(usage: Dict[str, int], model: str) -> float:
    return sum(usage.get(key, 0) * price for key, price in PRICES[model].items()) / 1e6


def spread(values: List[Optional[float]], fmt: str) -> str:
    """'median [min–max]'; one value alone; '—' with no data."""
    vals = [v for v in values if v is not None]
    if not vals:
        return "—"
    if len(vals) == 1:
        return fmt % vals[0]
    return "%s [%s–%s]" % (fmt % statistics.median(vals), fmt % min(vals), fmt % max(vals))


# ------------------------------------------------------------------ running
def subprocess_runner(argv: List[str], run_dir: Path) -> int:
    with open(run_dir / "stdout.log", "w") as out, open(run_dir / "trace.log", "w") as err:
        return subprocess.run(argv, cwd=str(PROJECT_ROOT), stdout=out, stderr=err).returncode


def command(run: Run, repo: str, args, run_dir: Path) -> List[str]:
    base = [sys.executable, str(PROJECT_ROOT / "main.py")]
    shared = ["--engine", run.engine, "--model", args.model, "--max-turns", str(args.max_turns)]
    if run.env == "local":
        return base + ["local", repo] + shared + ["--metrics-json", str(run_dir / "metrics.json")]
    return base + ["remote", repo] + shared + ["--out", str(run_dir)]


def filtered_copy(src: str, dest: Path) -> Path:
    """Extract the tarball an E2B run would upload (ignored dirs, secrets and old overviews left out)."""
    from overview_agent.remote import make_tarball  # remote imports e2b, which only the host has

    with tarfile.open(fileobj=io.BytesIO(make_tarball(src)), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    return dest / "repo"


def finished(run_dir: Path) -> bool:
    marker = run_dir / "bench.json"
    return marker.is_file() and json.loads(marker.read_text()).get("exit_code") == 0


def execute(run: Run, repo: str, args, out: Path, runner: Runner) -> int:
    run_dir = out / run.rel
    if run_dir.exists():  # an earlier failed attempt: keep it (its spend still counts), start clean
        run_dir.rename(run_dir.with_name("%s.failed-%s" % (run_dir.name, time.strftime("%Y%m%d-%H%M%S"))))
    run_dir.mkdir(parents=True)
    started = time.monotonic()
    if run.env == "local":
        with tempfile.TemporaryDirectory(prefix="overview-bench-") as tmp:
            copy = filtered_copy(repo, Path(tmp))
            code = runner(command(run, str(copy), args, run_dir), run_dir)
            if code == 0 and (copy / OVERVIEW_NAME).is_file():
                shutil.copy2(copy / OVERVIEW_NAME, run_dir / OVERVIEW_NAME)
    else:
        code = runner(command(run, repo, args, run_dir), run_dir)
    record = {"exit_code": code, "end_to_end_seconds": round(time.monotonic() - started, 1)}
    (run_dir / "bench.json").write_text(json.dumps(record, indent=2) + "\n")
    return code


def _load(path: Path) -> Optional[dict]:
    return json.loads(path.read_text()) if path.is_file() else None


def spent_usd(out: Path, model: str) -> float:
    """Every attempt's agent tokens (failed attempts included) plus every judge call."""
    total = sum(cost_usd(_load(p), model) for p in out.glob("*/*/*/metrics.json"))
    return total + sum(cost_usd(_load(p)["usage"], JUDGE_MODEL) for p in out.glob("*/*/*/judge.json"))


def next_run_estimate(out: Path, model: str) -> float:
    costs = [cost_usd(_load(p), model) for p in out.glob("*/*/*/metrics.json")]
    return max(costs) if costs else DEFAULT_RUN_ESTIMATE_USD


# ------------------------------------------------------------------ judging
def default_judge(repo: str):
    import anthropic

    from overview_agent import judge

    with tempfile.TemporaryDirectory(prefix="overview-bench-") as tmp:
        digest = judge.build_digest(str(filtered_copy(repo, Path(tmp))))
    client = anthropic.Anthropic()
    return lambda overview: judge.judge(overview, digest, client)


def judge_all(out: Path, runs: List[Run], judge_fn) -> None:
    pending = [r for r in runs if (out / r.rel / OVERVIEW_NAME).is_file() and not (out / r.rel / "judge.json").is_file()]
    random.Random(0).shuffle(pending)  # blind order: the judge never learns the engine, nor sees them grouped
    for run in pending:
        verdict = judge_fn((out / run.rel / OVERVIEW_NAME).read_text(encoding="utf-8"))
        (out / run.rel / "judge.json").write_text(json.dumps(verdict, indent=2) + "\n")


# ------------------------------------------------------------------ report
def _rows(model: str):
    def metric(key):
        return lambda m, b, j: m.get(key) if m else None

    rows = [
        ("Turns", metric("turns"), "%d"),
        ("Tool calls", metric("tool_calls"), "%d"),
        ("Tool errors", metric("tool_errors"), "%d"),
        ("Duplicate calls", metric("duplicate_calls"), "%d"),
        ("Rejected by RepoSandbox", metric("rejected_calls"), "%d"),
        ("Rejected by the SDK schema check", metric("schema_rejected_calls"), "%d"),
        ("Denied (permission)", metric("denied_calls"), "%d"),
        ("Write attempts", metric("write_attempts"), "%d"),
        ("Input, uncached", metric("input_tokens"), "%d"),
        ("Cache write", metric("cache_creation_input_tokens"), "%d"),
        ("Cache read", metric("cache_read_input_tokens"), "%d"),
        ("Output tokens", metric("output_tokens"), "%d"),
        ("Cost (USD)", lambda m, b, j: cost_usd(m, model) if m else None, "$%.3f"),
        ("Agent seconds", metric("wall_seconds"), "%.1f"),
        ("End-to-end seconds", lambda m, b, j: b.get("end_to_end_seconds") if b else None, "%.1f"),
    ]
    rows += [("Judge: %s" % c, (lambda c: lambda m, b, j: j["scores"][c]["score"] if j else None)(c), "%.1f")
             for c in CRITERIA]
    rows.append(("Judge: total (of 20)", lambda m, b, j: j["total"] if j else None, "%.1f"))
    return rows


def write_summary(out: Path, runs: List[Run], model: str, repo: str) -> Path:
    records = {r: tuple(_load(out / r.rel / name) for name in ("metrics.json", "bench.json", "judge.json"))
               for r in runs}
    lines = [
        "# Benchmark: api vs agent-sdk on %s" % Path(repo).name, "",
        "Model `%s`, judge `%s`. Each cell is the median with the [min–max] range. The local column is each "
        "engine's first, cold-cache run; E2B runs read that run's cache. Total spend: $%.2f." % (
            model, JUDGE_MODEL, spent_usd(out, model)), "",
        "| Metric | local · api | local · agent-sdk | E2B · api | E2B · agent-sdk |",
        "|---|---|---|---|---|",
    ]
    for label, get, fmt in _rows(model):
        cells = [spread([get(*records[r]) for r in runs if (r.env, r.engine) == col], fmt) for col in COLUMNS]
        lines.append("| %s | %s |" % (label, " | ".join(cells)))
    first_ok = []
    for col in COLUMNS:
        values = [records[r][0].get("first_write_ok") for r in runs if (r.env, r.engine) == col and records[r][0]]
        first_ok.append("%d/%d" % (sum(1 for v in values if v), len(values)) if values else "—")
    lines.append("| First write accepted | %s |" % " | ".join(first_ok))
    failed = [r.rel for r in runs if records[r][1] and records[r][1]["exit_code"] != 0]
    if failed:
        lines += ["", "Failed runs: %s" % ", ".join(failed)]
    lines += _spot_check(out, runs, records)
    path = out / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _spot_check(out: Path, runs: List[Run], records) -> List[str]:
    judged = {e: [r for r in runs if r.engine == e and records[r][2]] for e in ENGINES}
    pairs = [(abs(records[a][2]["total"] - records[b][2]["total"]), a, b)
             for a in judged["api"] for b in judged["agent-sdk"]]
    if not pairs:
        return []
    _, a, b = max(pairs, key=lambda p: p[0])
    lines = ["", "## Spot check", "",
             "The api and agent-sdk overviews with the largest gap in judge total. Read both and check the judge."]
    for run in (a, b):
        lines += ["", "### %s (judge total %d)" % (run.rel, records[run][2]["total"]), "",
                  (out / run.rel / OVERVIEW_NAME).read_text(encoding="utf-8").rstrip()]
    return lines


# ------------------------------------------------------------------ entry
def run_bench(repo: str, args, runner: Runner = subprocess_runner, judge_fn=None) -> int:
    if args.model not in PRICES:
        trace("error: no prices for model %s (add it to PRICES in overview_agent/bench.py)" % args.model)
        return 1
    if not Path(repo).is_dir():
        trace("error: not a directory: %s" % repo)
        return 1
    repo = str(Path(repo).resolve())
    out = Path(args.out).expanduser().resolve() if args.out else PROJECT_ROOT / "bench" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    runs = plan_runs()
    for run in runs:
        if finished(out / run.rel):
            trace("skip %s (already done)" % run.rel)
            continue
        spent, estimate = spent_usd(out, args.model), next_run_estimate(out, args.model)
        if spent + estimate > args.max_usd:
            trace("error: stopping before %s: $%.2f spent, and the next run (~$%.2f) could pass --max-usd $%.2f"
                  % (run.rel, spent, estimate, args.max_usd))
            write_summary(out, runs, args.model, repo)
            return 1
        trace("== %s ($%.2f spent so far)" % (run.rel, spent))
        if execute(run, repo, args, out, runner) != 0:
            trace("error: %s failed; see %s. Fix it, then resume with --out %s"
                  % (run.rel, out / run.rel / "trace.log", out))
            write_summary(out, runs, args.model, repo)
            return 1
    try:
        judge_all(out, runs, judge_fn or default_judge(repo))
    except JudgeError as e:
        trace("error: judging failed: %s. Resume with --out %s to judge the rest" % (e, out))
        write_summary(out, runs, args.model, repo)
        return 1
    path = write_summary(out, runs, args.model, repo)
    print("Wrote %s" % path)
    return 0
