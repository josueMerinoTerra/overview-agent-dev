"""What a run did, counted by the same code for both engines: tool calls through RepoSandbox, and the run report."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple

from overview_agent.config import trace
from overview_agent.overview_format import OVERVIEW_NAME
from overview_agent.sandbox import TIER3_MAX_FILES, RepoSandbox, ToolError

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class ToolRecorder:
    """Runs each tool call in the sandbox and counts what happened."""

    def __init__(self, sandbox: RepoSandbox, log: Callable[[str], None] = trace) -> None:
        self.sandbox, self.log = sandbox, log
        self.counts: Dict[str, object] = dict.fromkeys(
            ("tool_calls", "tool_errors", "write_attempts", "duplicate_calls", "rejected_calls"), 0)
        self.counts["first_write_ok"] = None
        self._seen: Set[str] = set()

    def call(self, name: str, args: dict) -> Tuple[str, bool]:
        self.log("  -> %s %s" % (name, args))
        key = name + json.dumps(args, sort_keys=True, default=str)
        if key in self._seen:
            self.counts["duplicate_calls"] += 1
        self._seen.add(key)
        try:
            out, is_error = self.sandbox.call(name, args), False
        except ToolError as e:  # a rule in RepoSandbox said no: ignored file, Tier 3 budget, invalid overview
            out, is_error = str(e), True
            self.counts["rejected_calls"] += 1
        except Exception as e:  # a tool bug must not kill the run; let the model see it
            out, is_error = "internal tool error: %s" % e, True
        self.counts["tool_calls"] += 1
        if name == "write_overview":
            self.counts["write_attempts"] += 1
            if self.counts["first_write_ok"] is None:
                self.counts["first_write_ok"] = not is_error
        if is_error:
            self.counts["tool_errors"] += 1
            self.log("     ! %s" % (out.splitlines()[0] if out else ""))
        return out, is_error


def finish_run(engine: str, model: str, sandbox: RepoSandbox, recorder: ToolRecorder, usage: Dict[str, int],
               turns: int, started: float, final_text: str, metrics_json: str = "",
               extra: Optional[dict] = None, error: str = "") -> int:
    """Print the summary, write the metrics, return the exit code. Ground truth comes from the sandbox."""
    print(final_text)
    print("\n---")
    print("Tier 3 files read: %d/%d" % (len(sandbox.tier3), TIER3_MAX_FILES))
    for rel, q in sandbox.tier3.items():
        print("  %s -> question %d" % (rel, q))
    metrics = dict(engine=engine, turns=turns, **recorder.counts)
    metrics.update((k, usage.get(k, 0)) for k in USAGE_KEYS)
    metrics.update(extra or {})
    metrics.update(
        model=model, wall_seconds=round(time.monotonic() - started, 1),
        tier3_files=len(sandbox.tier3), overview_written=sandbox.overview_written,
    )
    print("Run metrics: %d turns, %d tool calls (%d errors), tokens in/out %d/%d, cache write/read %d/%d, %.1fs" % (
        metrics["turns"], metrics["tool_calls"], metrics["tool_errors"],
        metrics["input_tokens"], metrics["output_tokens"],
        metrics["cache_creation_input_tokens"], metrics["cache_read_input_tokens"], metrics["wall_seconds"],
    ))
    if metrics_json:
        Path(metrics_json).write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    if error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    if sandbox.overview_written:
        print("Wrote %s" % (sandbox.root / OVERVIEW_NAME))
        return 0
    print("error: %s was not written" % OVERVIEW_NAME, file=sys.stderr)
    return 1
