# Agent SDK Engine + Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second engine (`--engine agent-sdk`) that runs the same overview task on the Claude Agent SDK, and a
`main.py bench` command that runs both engines on terra-agents-backend (locally and in E2B), judges every overview
blind, and writes a comparison table.

**Architecture:** The pieces both engines share (the task text, tool-call counting, the end-of-run report) move into
`task.py` and `recorder.py`. `agent.py` keeps its hand-written loop; the new `sdk_agent.py` wraps the same
`RepoSandbox` methods as in-process MCP tools and lets `ClaudeSDKClient` run the loop. `bench.py` runs each benchmark
run as a separate `main.py local|remote` process, and `judge.py` scores the overviews with one Messages API call each.

**Tech Stack:** Python 3.12, `anthropic`, `claude-agent-sdk==0.2.164`, `e2b==2.10.2`, `unittest`.

**Spec:** `docs/specs/agent-sdk-engine.md`

## Global Constraints

- Python 3.10+ is required by `claude-agent-sdk`; the project venv moves to Python 3.12 (Task 1). Keep the existing
  style: `from __future__ import annotations`, `typing` imports, one module docstring per file.
- Pin `claude-agent-sdk==0.2.164` in `requirements.txt`, and install the same pin in the E2B template.
- Agent model `claude-sonnet-5-5` for both engines. Judge model `claude-sonnet-5-5`.
- Prices, USD per million tokens: input 2.00, output 10.00, cache write 2.50, cache read 0.20.
- `agent-sdk` engine: `max_budget_usd=0.50` per run, `effort="high"` (the API default the `api` engine gets by not
  setting it).
- `bench`: 8 runs (per engine: 1 local, then 3 E2B), `--max-usd` default 3.00.
- Never remove either cache breakpoint in `overview_agent/agent.py`; `tests/test_agent.py` asserts both.
- No module imports `overview_agent.agent` except `main.py`.
- `python main.py local` with the default `api` engine must not import `e2b` or `claude_agent_sdk`.
- Tests are offline: `.venv/bin/python -m unittest -v`. No API keys, no network.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **The developer's Claude Code setup leaking into a local `agent-sdk` run** (`~/.claude` settings, CLAUDE.md,
  plugins, memory, MCP connectors). Expected: none of it loads. Pinned by Task 3's options test.
- **The model reaching a built-in tool** (Bash, Read, Write) on a local run. Expected: no built-ins exist, anything
  else is denied, and denials are counted. Pinned by Task 3's options and metrics tests.
- **Resuming a bench after a failed run.** Expected: finished runs are skipped, the failed run is re-run (not
  skipped because a `metrics.json` exists), and the failed attempt's cost still counts toward the spend guard.
  Pinned by Task 7's resume test.
- **The real terra-agents-backend folder** (it has an untracked `PROJECT_OVERVIEW.md`). Expected: local bench runs
  work on a filtered temp copy and never write to or read secrets from the source folder. Pinned by Task 7's
  filtered-copy test.
- **`--engine agent-sdk` on a machine without the package** (or on Python 3.9). Expected: one `error:` line that
  says what to install, exit 1, no traceback. Pinned by Task 4's missing-package test.

---

### Task 1: Python 3.12 venv and the new dependency

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Modify: `.env.example`

**Interfaces:**
- Consumes: nothing.
- Produces: a `.venv` on Python 3.12 with `claude_agent_sdk` importable; `bench/` ignored by git.

- [ ] **Step 1: Install Python 3.12 and recreate the venv**

The current `.venv` is Python 3.9.6 (`/Library/Developer/CommandLineTools`), and no 3.10+ interpreter is installed.

```bash
brew install python@3.12
cd /Users/josue.merino/learning/claude-code/overview-agent-dev
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python --version
```
Expected: `Python 3.12.x`

- [ ] **Step 2: Pin the dependency**

`requirements.txt` becomes:
```
anthropic
claude-agent-sdk==0.2.164
e2b==2.10.2
```

- [ ] **Step 3: Install and run the existing suite on 3.12**

```bash
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"
.venv/bin/python -m unittest -v 2>&1 | tail -3
```
Expected: `0.2.164`, then `OK`. If any existing test fails on 3.12, stop and fix that first (it is a 3.12
compatibility issue, not part of this feature).

- [ ] **Step 4: Ignore bench results and document the engine variable**

Append to `.gitignore`:
```
bench/
```
Append to `.env.example`, after the `OVERVIEW_MAX_TOKENS` block (keep the file's comment style):
```
# Which engine runs the agent: api (hand-written Messages API loop) or agent-sdk (Claude Agent SDK).
OVERVIEW_ENGINE=api
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore .env.example
git commit -m "Move to Python 3.12 and add claude-agent-sdk 0.2.164

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Shared task text, tool recorder and run report; `agent.py` uses them

**Files:**
- Create: `overview_agent/task.py`
- Create: `overview_agent/recorder.py`
- Modify: `overview_agent/agent.py`
- Modify: `overview_agent/config.py` (add `ENGINES`)
- Test: `tests/test_recorder.py` (new), `tests/test_agent.py`

**Interfaces:**
- Consumes: `RepoSandbox.call(name, args) -> str`, `ToolError`, `TIER3_MAX_FILES` (`sandbox.py`); `OVERVIEW_NAME`;
  `trace`.
- Produces:
  - `config.ENGINES = ("api", "agent-sdk")`
  - `task.load_instructions() -> str`, `task.FIRST_MESSAGE: str`, `task.REMINDER: str`
  - `recorder.USAGE_KEYS: tuple[str, ...]` = `("input_tokens", "output_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens")`
  - `recorder.ToolRecorder(sandbox, log=trace)` with `.call(name: str, args: dict) -> tuple[str, bool]` and
    `.counts: dict` (keys `tool_calls`, `tool_errors`, `write_attempts`, `duplicate_calls`, `rejected_calls`,
    `first_write_ok`)
  - `recorder.finish_run(engine, model, sandbox, recorder, usage, turns, started, final_text, metrics_json="",
    extra=None, error="") -> int`

- [ ] **Step 1: Check who imports `load_instructions`**

Run: `grep -rn "load_instructions" main.py overview_agent tests`
Expected: only `overview_agent/agent.py` (definition and one call). If anything else imports it, update that import
in Step 6 too.

- [ ] **Step 2: Write the failing recorder tests**

`tests/test_recorder.py`:
```python
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
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_recorder -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'overview_agent.recorder'`

- [ ] **Step 4: Create `overview_agent/task.py`**

```python
"""The task both engines give the model: the instructions, the first message and the one-time reminder."""
from __future__ import annotations

import sys
from pathlib import Path

from overview_agent.overview_format import OVERVIEW_NAME

FIRST_MESSAGE = (
    "Create %s for the repository at the sandbox root (paths are relative to it: '.'). "
    "Follow Stage 1, 2 and 3 of your instructions." % OVERVIEW_NAME
)
REMINDER = "You have not written %s yet. Call write_overview." % OVERVIEW_NAME


def load_instructions() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "overview_agent.md"
    if not path.is_file():
        sys.exit("error: prompts/overview_agent.md not found in the overview_agent package")
    return path.read_text(encoding="utf-8")
```

- [ ] **Step 5: Create `overview_agent/recorder.py`**

```python
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
```

Add to `overview_agent/config.py`, under `DEFAULT_MODEL`:
```python
ENGINES = ("api", "agent-sdk")  # api: our Messages API loop (agent.py); agent-sdk: Claude Agent SDK (sdk_agent.py)
```

- [ ] **Step 6: Make `agent.py` use them**

Replace the module's imports and `load_instructions` with:
```python
from __future__ import annotations

import sys
import time

import anthropic

from overview_agent.config import trace
from overview_agent.recorder import USAGE_KEYS, ToolRecorder, finish_run
from overview_agent.sandbox import RepoSandbox, ToolError
from overview_agent.task import FIRST_MESSAGE, REMINDER, load_instructions
from overview_agent.tool_schemas import TOOLS
```
In `run()`:
- the first user message becomes `messages = [{"role": "user", "content": FIRST_MESSAGE}]`
- replace the `usage_keys` / `metrics` setup with:
  ```python
  recorder = ToolRecorder(sandbox)
  usage = dict.fromkeys(USAGE_KEYS, 0)
  turns = 0
  ```
- after each response: `turns = turn` and `for key in USAGE_KEYS: usage[key] += getattr(resp.usage, key, 0) or 0`
- the reminder becomes `messages.append({"role": "user", "content": REMINDER})`
- the tool loop becomes:
  ```python
  results = []
  for block in (b for b in resp.content if b.type == "tool_use"):
      out, is_error = recorder.call(block.name, block.input)
      results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
  messages.append({"role": "user", "content": results})
  ```
- everything after the `for … else` becomes:
  ```python
  # The api engine has no client-side schema check and no permission layer, so these are always 0.
  extra = {"schema_rejected_calls": 0, "denied_calls": 0}
  return finish_run("api", model, sandbox, recorder, usage, turns, started, final_text, metrics_json, extra)
  ```
Keep the `client.messages.create(...)` call, both `cache_control` breakpoints, the comment above them, and the
`sys.exit` error handling exactly as they are. Update the module docstring's first line to: `"""The api engine: a
hand-written Messages API loop that runs each tool call in RepoSandbox and records metrics.`

- [ ] **Step 7: Extend `tests/test_agent.py` with the metrics shape**

Add to `AgentLoopTests`:
```python
    def test_metrics_name_the_engine_and_the_shared_counters(self):
        fake = SimpleNamespace(messages=FakeMessages())
        with tempfile.TemporaryDirectory() as repo, \
                mock.patch.object(agent.anthropic, "Anthropic", return_value=fake), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = os.path.join(repo, "m.json")
            agent.run(repo, "claude-sonnet-5-5", max_turns=3, metrics_json=path)
            with open(path) as fh:
                metrics = json.load(fh)
        self.assertEqual(metrics["engine"], "api")
        self.assertEqual(metrics["turns"], 2)
        for key in ("duplicate_calls", "rejected_calls", "schema_rejected_calls", "denied_calls"):
            self.assertEqual(metrics[key], 0)
```
and add `import json` and `import os` to the test's imports.

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_recorder tests.test_agent -v`
Expected: PASS (both cache-breakpoint assertions included).
Then: `.venv/bin/python -m unittest -v 2>&1 | tail -3` → `OK`

- [ ] **Step 9: Commit**

```bash
git add overview_agent/task.py overview_agent/recorder.py overview_agent/agent.py overview_agent/config.py \
        tests/test_recorder.py tests/test_agent.py
git commit -m "Share the task text, tool counting and run report between engines

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The `agent-sdk` engine

**Files:**
- Create: `overview_agent/sdk_agent.py`
- Test: `tests/test_sdk_agent.py` (new)

**Interfaces:**
- Consumes: `ToolRecorder`, `finish_run`, `USAGE_KEYS` (`recorder.py`); `FIRST_MESSAGE`, `REMINDER`,
  `load_instructions` (`task.py`); `TOOLS` (`tool_schemas.py`); from `claude_agent_sdk`: `AssistantMessage`,
  `ClaudeAgentOptions`, `ClaudeSDKClient`, `ClaudeSDKError`, `ResultMessage`, `TextBlock`, `ToolUseBlock`,
  `create_sdk_mcp_server`, `tool`.
- Produces:
  - `sdk_agent.run(root: str, model: str, max_turns: int, metrics_json: str = "", client_factory=ClaudeSDKClient)
    -> int`
  - `sdk_agent.make_tools(recorder) -> list[SdkMcpTool]`
  - `sdk_agent.build_options(model, max_turns, workdir, tools) -> ClaudeAgentOptions`
  - `sdk_agent.SERVER = "overview"`, `sdk_agent.MAX_BUDGET_USD = 0.50`, `sdk_agent.EFFORT = "high"`
  - extra metrics keys: `schema_rejected_calls`, `denied_calls`, `sdk_cost_usd`

SDK facts used here (checked in the 0.2.164 wheel): `tool(name, description, input_schema)` passes a dict that has
`"type"` and `"properties"` through unchanged as the JSON schema; the handler returns `{"content": [...],
"is_error": bool}`; the SDK validates input against the schema before calling the handler and answers invalid input
itself (so such calls never reach `ToolRecorder`); `ClaudeSDKClient.receive_response()` stops after the
`ResultMessage`; error results may also raise a `ClaudeSDKError` subclass; `ENABLE_TOOL_SEARCH=false` in `env`
turns tool search off; `effort` is an option.

- [ ] **Step 1: Write the failing tests**

`tests/test_sdk_agent.py`:
```python
"""Offline tests for the agent-sdk engine (a fake ClaudeSDKClient, no CLI, no API key)."""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from claude_agent_sdk import AssistantMessage, CLINotFoundError, ResultMessage, ToolUseBlock

from overview_agent import agent, sdk_agent
from overview_agent.recorder import ToolRecorder
from overview_agent.sandbox import RepoSandbox
from overview_agent.task import FIRST_MESSAGE, REMINDER, load_instructions
from overview_agent.tool_schemas import TOOLS
from tests.test_agent import FakeMessages
from tests.test_sandbox import GOOD

USAGE = {"input_tokens": 5, "output_tokens": 7, "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13}


def result(subtype="success", turns=2, text="done", denials=None):
    return ResultMessage(subtype=subtype, duration_ms=10, duration_api_ms=8, is_error=subtype != "success",
                         num_turns=turns, session_id="s1", total_cost_usd=0.01, usage=dict(USAGE), result=text,
                         permission_denials=denials)


def tool_use(i, name, args):
    return ToolUseBlock(id="t%d" % i, name="mcp__overview__" + name, input=args)


class FakeClient:
    """Stands in for ClaudeSDKClient: each query() runs the next scripted step, which may call our tools."""

    def __init__(self, options, script, tools):
        self.options, self.script, self.tools = options, list(script), tools
        self.prompts, self.pending = [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.prompts.append(prompt)
        self.pending = await self.script.pop(0)(self)

    async def receive_response(self):
        for message in self.pending:
            yield message


async def writes_overview(client):
    await client.tools["write_overview"].handler({"content": GOOD})
    return [AssistantMessage(content=[tool_use(1, "write_overview", {"content": GOOD})], model="m"), result()]


async def says_done(client):
    return [result()]


async def hits_max_turns(client):
    return [result(subtype="error_max_turns", turns=25)]


async def one_call_reaches_us_one_does_not(client):
    await client.tools["list_tree"].handler({"path": "."})
    blocks = [tool_use(1, "list_tree", {"path": "."}),             # reached RepoSandbox
              tool_use(2, "list_tree", {"max_depth": "deep"}),     # failed the SDK's schema check
              ToolUseBlock(id="t3", name="Bash", input={})]        # denied by the permission layer
    return [AssistantMessage(content=blocks, model="m"), result(denials=[{"tool_name": "Bash"}])]


class SdkEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("# Acme\nBooks appointments.\n")
        self.metrics = Path(self._tmp.name) / "metrics.json"

    def run_engine(self, *script, factory=None):
        clients, real_make_tools = [], sdk_agent.make_tools

        def capture(recorder):
            tools = real_make_tools(recorder)
            capture.tools = {t.name: t for t in tools}
            return tools

        def default_factory(options):
            clients.append(FakeClient(options, script, capture.tools))
            return clients[-1]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sdk_agent, "make_tools", side_effect=capture), redirect_stdout(out), \
                redirect_stderr(err):
            code = sdk_agent.run(str(self.repo), "claude-sonnet-5-5", 25, str(self.metrics),
                                 client_factory=factory or default_factory)
        metrics = json.loads(self.metrics.read_text()) if self.metrics.exists() else None
        return code, (clients[0] if clients else None), metrics, err.getvalue()

    def test_options_lock_down_tools_and_isolate_the_machine_setup(self):
        with tempfile.TemporaryDirectory() as workdir:
            o = sdk_agent.build_options("claude-sonnet-5-5", 9, workdir, [])
        self.assertEqual(o.tools, [])
        self.assertEqual(o.allowed_tools, ["mcp__overview__" + t["name"] for t in TOOLS])
        self.assertEqual(o.permission_mode, "dontAsk")
        self.assertEqual(o.setting_sources, [])
        self.assertTrue(o.strict_mcp_config)
        self.assertEqual(o.env, {"CLAUDE_CONFIG_DIR": workdir, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                                 "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "ENABLE_TOOL_SEARCH": "false"})
        self.assertEqual(str(o.cwd), workdir)
        self.assertEqual(list(o.mcp_servers), ["overview"])
        self.assertEqual(o.system_prompt, load_instructions())
        self.assertEqual((o.model, o.max_turns, o.max_budget_usd, o.effort),
                         ("claude-sonnet-5-5", 9, 0.50, "high"))

    def test_one_tool_per_schema_with_the_same_name_description_and_schema(self):
        rec = ToolRecorder(RepoSandbox(str(self.repo), log=lambda m: None), log=lambda m: None)
        tools = sdk_agent.make_tools(rec)
        self.assertEqual([(t.name, t.description, t.input_schema) for t in tools],
                         [(s["name"], s["description"], s["input_schema"]) for s in TOOLS])

    def test_a_sandbox_rejection_comes_back_as_is_error(self):
        (self.repo / ".env").write_text("SECRET=1\n")
        rec = ToolRecorder(RepoSandbox(str(self.repo), log=lambda m: None), log=lambda m: None)
        read_file = {t.name: t for t in sdk_agent.make_tools(rec)}["read_file"]
        reply = asyncio.run(read_file.handler({"path": ".env", "tier": 1}))
        self.assertTrue(reply["is_error"])
        self.assertEqual(rec.counts["rejected_calls"], 1)

    def test_a_written_overview_exits_0_with_the_same_metric_keys_as_the_api_engine(self):
        code, client, metrics, _ = self.run_engine(writes_overview)
        self.assertEqual(code, 0)
        self.assertEqual(client.prompts, [FIRST_MESSAGE])  # no reminder
        self.assertEqual(metrics["engine"], "agent-sdk")
        self.assertEqual((metrics["turns"], metrics["input_tokens"], metrics["cache_read_input_tokens"]), (2, 5, 13))
        self.assertEqual((metrics["write_attempts"], metrics["first_write_ok"]), (1, True))
        self.assertFalse(Path(client.options.cwd).exists())  # the temp config dir is removed

        api_metrics = Path(self._tmp.name) / "api.json"
        fake = SimpleNamespace(messages=FakeMessages())
        with mock.patch.object(agent.anthropic, "Anthropic", return_value=fake), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            agent.run(str(self.repo), "claude-sonnet-5-5", 3, metrics_json=str(api_metrics))
        self.assertEqual(set(metrics) - {"sdk_cost_usd"}, set(json.loads(api_metrics.read_text())))

    def test_the_reminder_is_sent_once_when_nothing_was_written(self):
        code, client, metrics, err = self.run_engine(says_done, says_done)
        self.assertEqual(code, 1)
        self.assertEqual(client.prompts, [FIRST_MESSAGE, REMINDER])
        self.assertEqual(metrics["turns"], 4)
        self.assertEqual(metrics["input_tokens"], 10)

    def test_the_turn_limit_is_named_metrics_are_written_and_no_reminder_follows(self):
        code, client, metrics, err = self.run_engine(hits_max_turns)
        self.assertEqual(code, 1)
        self.assertEqual(client.prompts, [FIRST_MESSAGE])
        self.assertIn("--max-turns", err)
        self.assertEqual(metrics["turns"], 25)

    def test_calls_that_never_reached_our_code_and_denials_are_counted(self):
        code, _, metrics, _ = self.run_engine(one_call_reaches_us_one_does_not, says_done)
        self.assertEqual(metrics["tool_calls"], 1)
        self.assertEqual(metrics["schema_rejected_calls"], 1)
        self.assertEqual(metrics["denied_calls"], 1)

    def test_an_sdk_failure_is_one_error_line_and_still_writes_metrics(self):
        class Broken:
            def __init__(self, options):
                pass

            async def __aenter__(self):
                raise CLINotFoundError("Claude Code not found")

            async def __aexit__(self, *exc):
                return False

        code, _, metrics, err = self.run_engine(factory=Broken)
        self.assertEqual(code, 1)
        self.assertIn("error: Agent SDK error:", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(metrics["tool_calls"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_sdk_agent -v`
Expected: FAIL with `ImportError: cannot import name 'sdk_agent'`

- [ ] **Step 3: Create `overview_agent/sdk_agent.py`**

```python
"""The agent-sdk engine: the same task and RepoSandbox tools, with the Claude Agent SDK running the loop.

The model gets only our five tools, served in-process as the MCP server "overview", and nothing from this
machine's Claude Code setup (settings, CLAUDE.md, plugins, memory, MCP connectors). Run it with
`python main.py local --engine agent-sdk`.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Set

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ClaudeSDKError, ResultMessage, TextBlock, ToolUseBlock,
    create_sdk_mcp_server, tool,
)

from overview_agent.config import trace
from overview_agent.recorder import USAGE_KEYS, ToolRecorder, finish_run
from overview_agent.sandbox import RepoSandbox, ToolError
from overview_agent.task import FIRST_MESSAGE, REMINDER, load_instructions
from overview_agent.tool_schemas import TOOLS

SERVER = "overview"
MAX_BUDGET_USD = 0.50
EFFORT = "high"  # the Messages API default for claude-sonnet-5-5, which the api engine gets by not setting it
LIMITS = {
    "error_max_turns": "stopped at the --max-turns limit",
    "error_max_budget_usd": "stopped at the $%.2f per-run budget" % MAX_BUDGET_USD,
}


def make_tools(recorder: ToolRecorder) -> List[Any]:
    """One in-process tool per TOOLS entry, with the same name, description and JSON schema."""
    return [tool(spec["name"], spec["description"], spec["input_schema"])(_handler(recorder, spec["name"]))
            for spec in TOOLS]


def _handler(recorder: ToolRecorder, name: str):
    async def handle(args: Dict[str, Any]) -> Dict[str, Any]:
        out, is_error = recorder.call(name, args)
        return {"content": [{"type": "text", "text": out}], "is_error": is_error}
    return handle


def build_options(model: str, max_turns: int, workdir: str, tools: List[Any]) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=load_instructions(),
        model=model, effort=EFFORT, max_turns=max_turns, max_budget_usd=MAX_BUDGET_USD,
        mcp_servers={SERVER: create_sdk_mcp_server(SERVER, tools=tools)},
        # Only our tools: no built-ins at all, ours pre-approved, anything else denied without asking.
        tools=[], allowed_tools=["mcp__%s__%s" % (SERVER, spec["name"]) for spec in TOOLS],
        permission_mode="dontAsk",
        # Nothing from this machine's Claude Code setup reaches the runtime agent.
        setting_sources=[], strict_mcp_config=True,
        env={"CLAUDE_CONFIG_DIR": workdir, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
             "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "ENABLE_TOOL_SEARCH": "false"},
        cwd=workdir,
    )


class _RunState:
    def __init__(self) -> None:
        self.usage = dict.fromkeys(USAGE_KEYS, 0)
        self.turns = 0
        self.final_text = ""
        self.error = ""
        self.limit = ""
        self.tool_uses: Set[str] = set()
        self.denied = 0
        self.sdk_cost = 0.0


async def _read(client, state: _RunState) -> None:
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    state.tool_uses.add(block.id)
                elif isinstance(block, TextBlock) and block.text.strip():
                    trace("\n%s" % block.text.strip())
        elif isinstance(msg, ResultMessage):
            state.turns += msg.num_turns
            for key in USAGE_KEYS:
                state.usage[key] += int((msg.usage or {}).get(key, 0) or 0)
            state.denied += len(msg.permission_denials or [])
            state.sdk_cost += msg.total_cost_usd or 0.0
            state.final_text = msg.result or state.final_text
            if msg.subtype in LIMITS:
                state.limit = LIMITS[msg.subtype]
                trace("warning: %s" % state.limit)
            elif msg.is_error or msg.subtype != "success":
                state.error = "the Agent SDK run failed (%s): %s" % (
                    msg.subtype, "; ".join(msg.errors or []) or msg.result or "no details")


async def _session(client_factory, options: ClaudeAgentOptions, sandbox: RepoSandbox, state: _RunState) -> None:
    try:
        async with client_factory(options=options) as client:
            await client.query(FIRST_MESSAGE)
            await _read(client, state)
            if not sandbox.overview_written and not (state.error or state.limit):
                await client.query(REMINDER)  # finished without writing the file: one reminder, as in agent.py
                await _read(client, state)
    except ClaudeSDKError as e:  # error results may also raise; keep the first, clearer reason
        if not (state.error or state.limit):
            state.error = "Agent SDK error: %s" % e


def run(root: str, model: str, max_turns: int, metrics_json: str = "", client_factory=ClaudeSDKClient) -> int:
    try:
        sandbox = RepoSandbox(root, log=trace)
    except ToolError as e:
        sys.exit("error: %s" % e)
    recorder = ToolRecorder(sandbox)
    state = _RunState()
    workdir = tempfile.mkdtemp(prefix="overview-sdk-")  # empty Claude Code config dir and cwd, never the repo
    started = time.monotonic()
    try:
        options = build_options(model, max_turns, workdir, make_tools(recorder))
        asyncio.run(_session(client_factory, options, sandbox, state))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    extra = {
        # tool_use blocks our code never saw: rejected by the SDK's schema check before reaching RepoSandbox
        "schema_rejected_calls": max(0, len(state.tool_uses) - recorder.counts["tool_calls"] - state.denied),
        "denied_calls": state.denied,
        "sdk_cost_usd": round(state.sdk_cost, 6),
    }
    return finish_run("agent-sdk", model, sandbox, recorder, state.usage, state.turns, started,
                      state.final_text, metrics_json, extra, state.error)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_sdk_agent -v`
Expected: PASS. If `ClaudeAgentOptions` rejects a field name, check `inspect.signature(ClaudeAgentOptions)` in the
installed package and fix the name in both the module and the test; do not drop the option.
Then: `.venv/bin/python -m unittest -v 2>&1 | tail -3` → `OK`

- [ ] **Step 5: Commit**

```bash
git add overview_agent/sdk_agent.py tests/test_sdk_agent.py
git commit -m "Add the agent-sdk engine: RepoSandbox tools over in-process MCP, locked down and isolated

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `--engine` on `local` and `remote`

**Files:**
- Modify: `main.py`
- Modify: `overview_agent/remote.py` (the agent command in `_run_in_sandbox`)
- Test: `tests/test_main.py`, `tests/test_remote.py`

**Interfaces:**
- Consumes: `config.ENGINES`; `agent.run(repo, model, max_turns, max_tokens, metrics_json)`;
  `sdk_agent.run(repo, model, max_turns, metrics_json)`.
- Produces: `args.engine` on the `local` and `remote` namespaces (default `$OVERVIEW_ENGINE` or `"api"`); the
  sandbox command `python main.py local … --engine <engine> …`.

- [ ] **Step 1: Write the failing CLI tests**

Add to `tests/test_main.py`, in `LocalCommandTests`:
```python
    def test_agent_sdk_engine_runs_sdk_agent_without_max_tokens(self):
        fake = mock.MagicMock()
        fake.run.return_value = 0
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.dict(sys.modules, {"overview_agent.sdk_agent": fake}), \
                mock.patch.object(main.agent, "run") as api_run:
            code = main.main(["local", "r", "--engine", "agent-sdk", "--metrics-json", "m.json"])
        self.assertEqual(code, 0)
        fake.run.assert_called_once_with("r", DEFAULT_MODEL, 25, "m.json")
        api_run.assert_not_called()

    def test_engine_defaults_to_overview_engine_from_the_environment(self):
        fake = mock.MagicMock()
        fake.run.return_value = 0
        with mock.patch.object(main, "load_dotenv"), \
                mock.patch.dict(os.environ, {"OVERVIEW_ENGINE": "agent-sdk", "ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.dict(sys.modules, {"overview_agent.sdk_agent": fake}):
            main.main(["local", "r"])
        fake.run.assert_called_once()

    def test_missing_agent_sdk_package_is_one_error_line(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.dict(sys.modules, {"overview_agent.sdk_agent": None}), redirect_stderr(err):
            code = main.main(["local", "r", "--engine", "agent-sdk"])
        self.assertEqual(code, 1)
        self.assertIn("error: the agent-sdk engine needs the claude-agent-sdk package", err.getvalue())
```
Add to `RemoteCommandTests.test_flags_and_env_defaults_reach_run_remote`: put `"OVERVIEW_ENGINE": "agent-sdk"` in
its `env` dict and add `self.assertEqual(args.engine, "agent-sdk")`.

Add to `MalformedEnvTests`:
```python
    def test_an_unknown_overview_engine_is_a_usage_error(self):
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"OVERVIEW_ENGINE": "gpt"}, clear=True), \
                mock.patch.object(main.agent, "run") as run, redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            main.main(["local", "r"])
        self.assertEqual(cm.exception.code, 2)
        run.assert_not_called()
```
Add to `EntryPointTests`:
```python
    def test_local_api_engine_works_without_the_agent_sdk_package(self):
        code = ("import sys; sys.modules['e2b'] = None; sys.modules['claude_agent_sdk'] = None\n"
                "import main\nmain.main(['local', '--help'])\n")
        result = subprocess.run([sys.executable, "-c", code], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--engine", result.stdout)
```

In `tests/test_remote.py`: add `engine="api"` to the `SimpleNamespace` in `RunRemoteTests.setUp`, and add:
```python
    def test_the_engine_is_forwarded_to_the_agent_command(self):
        self.args.engine = "agent-sdk"
        sbx = FakeSandbox()
        self.run_with(sbx)
        cmd, _ = sbx.agent_run()
        self.assertIn("--engine agent-sdk", cmd)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_main tests.test_remote -v`
Expected: FAIL (`unrecognized arguments: --engine`, `AttributeError: … engine`, the remote command lacks `--engine`).

- [ ] **Step 3: Implement**

In `main.py`, add `import importlib`, import `ENGINES` with the other config names, and update the docstring's usage lines to show
`[--engine api|agent-sdk]` on `local` and `remote`. In `build_parser()`, after `shared`:
```python
    engine = argparse.ArgumentParser(add_help=False)
    engine.add_argument("--engine", default=env("OVERVIEW_ENGINE", "api"), choices=ENGINES,
                        help="api: our Messages API loop; agent-sdk: the Claude Agent SDK (default: $OVERVIEW_ENGINE or api)")
```
and use `parents=[shared, engine]` for the `local` and `remote` subparsers.

In `main()`, after parsing (argparse does not check `choices` against a default that came from the environment):
```python
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "engine", "api") not in ENGINES:
        parser.error("OVERVIEW_ENGINE must be one of: %s" % ", ".join(ENGINES))
    return args.handler(args)
```
Replace the last line of `run_local` with:
```python
    if args.engine == "agent-sdk":
        try:  # import_module (not `from … import`) so tests can stand in a fake or a missing module via sys.modules
            sdk_agent = importlib.import_module("overview_agent.sdk_agent")  # needs claude-agent-sdk (Python 3.10+)
        except ImportError as e:
            trace("error: the agent-sdk engine needs the claude-agent-sdk package "
                  "(Python 3.10+; pip install -r requirements.txt): %s" % e)
            return 1
        return sdk_agent.run(args.repo, args.model, args.max_turns, args.metrics_json)
    return agent.run(args.repo, args.model, args.max_turns, args.max_tokens, args.metrics_json)
```
In `overview_agent/remote.py`, `_run_in_sandbox`:
```python
    cmd = "python main.py local %s --engine %s --model %s --max-turns %d --max-tokens %d --metrics-json %s" % (
        REPO_DIR, shlex.quote(args.engine), shlex.quote(args.model), args.max_turns, args.max_tokens, METRICS)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_main tests.test_remote -v`
Expected: PASS. Then the full suite → `OK`.

- [ ] **Step 5: Commit**

```bash
git add main.py overview_agent/remote.py tests/test_main.py tests/test_remote.py
git commit -m "Add --engine api|agent-sdk to local and remote

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: E2B template with the Agent SDK

**Files:**
- Modify: `overview_agent/e2b_template.py`
- Test: `tests/test_remote.py` (`TemplateTests`)

**Interfaces:**
- Consumes: the `claude-agent-sdk==…` pin in `requirements.txt`.
- Produces: template `overview-agent` with `anthropic` and `claude-agent-sdk==0.2.164`, 2 GB RAM.

- [ ] **Step 1: Write the failing test**

Add to `TemplateTests`:
```python
    def test_template_installs_the_same_agent_sdk_pin_as_requirements(self):
        from overview_agent import e2b_template
        from e2b import Template
        pin = next(line.strip() for line in (config.PROJECT_ROOT / "requirements.txt").read_text().splitlines()
                   if line.startswith("claude-agent-sdk"))
        self.assertIn("pip install %s" % pin, Template.to_dockerfile(e2b_template.template()))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_remote.TemplateTests -v`
Expected: FAIL (`'pip install claude-agent-sdk==0.2.164' not found`).

- [ ] **Step 3: Implement**

In `overview_agent/e2b_template.py`:
```python
AGENT_SDK = "claude-agent-sdk==0.2.164"  # keep in sync with requirements.txt (a test checks)


def template():
    return (Template().from_python_image("3.12").apt_install("git")
            .pip_install("anthropic").pip_install(AGENT_SDK))
```
In `build()`, change `memory_mb=1024` to `memory_mb=2048` (the Agent SDK runs the bundled Claude Code CLI as a
subprocess next to Python). Update the module docstring: the template is "Python, git, the anthropic SDK and the
Claude Agent SDK".

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_remote -v`
Expected: PASS (the existing `pip install anthropic` and no-secrets checks still pass).

- [ ] **Step 5: Commit**

```bash
git add overview_agent/e2b_template.py tests/test_remote.py
git commit -m "E2B template: add the Claude Agent SDK and 2 GB RAM

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The judge

**Files:**
- Create: `overview_agent/judge.py`
- Test: `tests/test_judge.py` (new)

**Interfaces:**
- Consumes: `RepoSandbox.list_tree`, `RepoSandbox.read_file`, `ToolError`; `USAGE_KEYS`.
- Produces:
  - `judge.JUDGE_MODEL = "claude-sonnet-5-5"`, `judge.CRITERIA = ("accuracy", "coverage", "what_not_how", "clarity")`
  - `judge.build_digest(repo: str) -> str`
  - `judge.judge(overview: str, digest: str, client) -> dict` with keys `model`, `scores` (`{criterion: {"score":
    int, "reason": str}}`), `total` (int, 4-20), `usage` (`{USAGE_KEYS: int}`)
  - `judge.JudgeError(Exception)`

- [ ] **Step 1: Write the failing tests**

`tests/test_judge.py`:
```python
"""Offline tests for judge.py (fake Messages client)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from overview_agent import judge


def scores(value=4):
    return {c: {"score": value, "reason": "ok"} for c in judge.CRITERIA}


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=10, output_tokens=20, cache_creation_input_tokens=30,
                                cache_read_input_tokens=40)
        content = [SimpleNamespace(type="thinking", thinking=""),
                   SimpleNamespace(type="text", text=json.dumps(self.payload))]
        return SimpleNamespace(content=content, stop_reason=self.stop_reason, usage=usage)


class JudgeTests(unittest.TestCase):
    def test_request_caches_the_digest_and_asks_for_structured_scores(self):
        fake = FakeMessages(scores())
        judge.judge("# Acme: Product Overview\nBooks appointments.", "DIGEST", SimpleNamespace(messages=fake))
        kw = fake.calls[0]
        self.assertEqual(kw["model"], judge.JUDGE_MODEL)
        self.assertIn("DIGEST", kw["system"][1]["text"])
        self.assertEqual(kw["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Books appointments.", kw["messages"][0]["content"])
        dumped = json.dumps(kw).lower()
        self.assertNotIn("agent-sdk", dumped)
        self.assertNotIn("engine", dumped)

    def test_scores_total_and_usage_come_back(self):
        out = judge.judge("x", "d", SimpleNamespace(messages=FakeMessages(scores(3))))
        self.assertEqual(out["total"], 12)
        self.assertEqual(out["scores"]["coverage"], {"score": 3, "reason": "ok"})
        self.assertEqual(out["usage"]["cache_read_input_tokens"], 40)

    def test_out_of_range_score_is_a_judge_error(self):
        with self.assertRaises(judge.JudgeError):
            judge.judge("x", "d", SimpleNamespace(messages=FakeMessages(scores(9))))

    def test_refusal_is_a_judge_error(self):
        with self.assertRaises(judge.JudgeError):
            judge.judge("x", "d", SimpleNamespace(messages=FakeMessages(scores(), stop_reason="refusal")))

    def test_digest_has_the_tree_and_tier_1_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("Acme books appointments.\n")
            (root / "package.json").write_text('{"name": "acme"}\n')
            (root / ".env").write_text("SECRET=hunter2\n")
            (root / "src").mkdir()
            (root / "src" / "app.ts").write_text("const internal = 1;\n")
            (root / "docs").mkdir()
            (root / "docs" / "index.md").write_text("Docs home.\n")
            digest = judge.build_digest(tmp)
        self.assertIn("## File tree", digest)
        self.assertIn("Acme books appointments.", digest)
        self.assertIn('"name": "acme"', digest)
        self.assertIn("Docs home.", digest)
        self.assertNotIn("hunter2", digest)
        self.assertNotIn("const internal", digest)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_judge -v`
Expected: FAIL with `ImportError: cannot import name 'judge'`

- [ ] **Step 3: Create `overview_agent/judge.py`**

```python
"""Scores one PROJECT_OVERVIEW.md against its repository, blind to which engine wrote it. Used by `main.py bench`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from overview_agent.recorder import USAGE_KEYS
from overview_agent.sandbox import RepoSandbox, ToolError

JUDGE_MODEL = "claude-sonnet-5-5"
CRITERIA = ("accuracy", "coverage", "what_not_how", "clarity")
RUBRIC = """You grade PROJECT_OVERVIEW.md files: product overviews that say what a product does, not how it is built.
You get the repository's file tree and its curated docs and manifests, then one overview.
Score each criterion from 1 (poor) to 5 (excellent) and give a one-sentence reason:
- accuracy: every claim is supported by the repository material; nothing is invented.
- coverage: it names the core user, the key features and the main workflow.
- what_not_how: it describes behavior and value, not implementation (frameworks, file layout, code details).
- clarity: a new teammate understands the product after one read.
Judge only what is written. Length and formatting are not criteria."""
SCHEMA = {
    "type": "object",
    "properties": {
        c: {
            "type": "object",
            "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["score", "reason"],
            "additionalProperties": False,
        }
        for c in CRITERIA
    },
    "required": list(CRITERIA),
    "additionalProperties": False,
}


class JudgeError(Exception):
    """The judge gave no usable verdict for one overview."""


def build_digest(repo: str) -> str:
    """The file tree plus every root-level and docs/ file the agents could read for free (Tier 1)."""
    sandbox = RepoSandbox(repo, log=lambda msg: None)
    parts = ["## File tree\n" + sandbox.list_tree(".", 2)]
    root = Path(repo)
    candidates: List[Path] = sorted(p for p in root.iterdir() if p.is_file())
    if (root / "docs").is_dir():
        candidates += sorted(p for p in (root / "docs").iterdir() if p.is_file())
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        try:
            parts.append("## %s\n%s" % (rel, sandbox.read_file(rel, tier=1)))
        except ToolError:  # not Tier 1, ignored or binary: the agents could not read it for free either
            continue
    return "\n\n".join(parts)


def judge(overview: str, digest: str, client) -> Dict[str, Any]:
    resp = client.messages.create(
        model=JUDGE_MODEL, max_tokens=4000,
        system=[
            {"type": "text", "text": RUBRIC},
            {"type": "text", "text": "# Repository material\n\n" + digest, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": "Score this overview.\n\n<overview>\n%s\n</overview>" % overview}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise JudgeError("judge stopped with stop_reason=%s" % resp.stop_reason)
    try:
        scores = json.loads("".join(b.text for b in resp.content if b.type == "text"))
        total = 0
        for c in CRITERIA:
            if not 1 <= int(scores[c]["score"]) <= 5:
                raise JudgeError("score out of range for %s: %s" % (c, scores[c]["score"]))
            total += int(scores[c]["score"])
    except (ValueError, KeyError, TypeError) as e:
        raise JudgeError("unreadable verdict: %s" % e)
    usage = {k: getattr(resp.usage, k, 0) or 0 for k in USAGE_KEYS}
    return {"model": JUDGE_MODEL, "scores": scores, "total": total, "usage": usage}
```
Note: `JudgeError` raised inside the `try` is not a `ValueError`, so it propagates unchanged.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_judge -v`
Expected: PASS. Then the full suite → `OK`.

- [ ] **Step 5: Commit**

```bash
git add overview_agent/judge.py tests/test_judge.py
git commit -m "Add the blind LLM judge for overviews

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The bench runner and `main.py bench`

**Files:**
- Create: `overview_agent/bench.py`
- Modify: `main.py` (the `bench` subcommand)
- Test: `tests/test_bench.py` (new), `tests/test_main.py`

**Interfaces:**
- Consumes: `config.ENGINES`, `PROJECT_ROOT`, `trace`; `OVERVIEW_NAME`; `USAGE_KEYS`;
  `remote.make_tarball(path) -> bytes`; `judge.build_digest`, `judge.judge`, `judge.JUDGE_MODEL`, `judge.CRITERIA`,
  `judge.JudgeError`.
- Produces:
  - `bench.Run(env, engine, n)` with `.rel -> "env/engine/run-n"`
  - `bench.plan_runs() -> list[Run]`, `bench.cost_usd(usage, model) -> float`, `bench.spread(values, fmt) -> str`
  - `bench.run_bench(repo: str, args, runner=subprocess_runner, judge_fn=None) -> int`, where `args` has `model`,
    `max_turns`, `out`, `max_usd`, and `runner(argv: list[str], run_dir: Path) -> int`, and
    `judge_fn(overview: str) -> dict` (the `judge.judge` result shape)

- [ ] **Step 1: Write the failing tests**

`tests/test_bench.py`:
```python
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
```

Add to `tests/test_main.py`:
```python
class BenchCommandTests(unittest.TestCase):
    def test_bench_flags_reach_run_bench(self):
        from overview_agent import bench
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(bench, "run_bench", return_value=0) as run:
            code = main.main(["bench", "/tmp/terra", "--max-usd", "2", "--out", "bench/x"])
        self.assertEqual(code, 0)
        repo, args = run.call_args[0]
        self.assertEqual((repo, args.max_usd, args.out, args.model, args.max_turns),
                         ("/tmp/terra", 2.0, "bench/x", DEFAULT_MODEL, 25))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_bench tests.test_main -v`
Expected: FAIL with `ImportError: cannot import name 'bench'` and `invalid choice: 'bench'`.

- [ ] **Step 3: Create `overview_agent/bench.py`**

```python
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
from overview_agent.recorder import USAGE_KEYS

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
```

- [ ] **Step 4: Add the `bench` subcommand to `main.py`**

Add to the docstring's usage lines:
`python main.py bench <local-path> [--model M] [--max-turns N] [--out DIR] [--max-usd X]`.
In `build_parser()`, before `build-template`:
```python
    bench = commands.add_parser(
        "bench", parents=[shared], help="benchmark both engines on one repo (8 runs, about $2)",
        description="Run both engines on one local repo (1 local + 3 E2B runs each), judge the overviews, "
                    "and write bench/<timestamp>/summary.md.")
    bench.add_argument("repo", help="local repository folder (never written: runs use a filtered copy)")
    bench.add_argument("--out", default="", help="results folder; pass an earlier one to resume (default: bench/<timestamp>/)")
    bench.add_argument("--max-usd", type=float, default=3.0, help="stop before the estimated spend passes this (default 3.00)")
    bench.set_defaults(handler=run_bench)
```
and the handler:
```python
def run_bench(args: argparse.Namespace) -> int:
    from overview_agent import bench  # imports remote (e2b) lazily, like the remote command

    try:
        return bench.run_bench(args.repo, args)
    except KeyboardInterrupt:
        trace("interrupted; resume with --out pointing at the same folder")
        return 130
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_bench tests.test_main -v`
Expected: PASS. Then the full suite → `OK`.

- [ ] **Step 6: Commit**

```bash
git add overview_agent/bench.py main.py tests/test_bench.py tests/test_main.py
git commit -m "Add main.py bench: both engines, local and E2B, judged blind, resumable, spend-capped

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the commands and modules from Tasks 2-7.
- Produces: docs that match the code.

- [ ] **Step 1: README "Engines" section**

Add after the remote-run section (match the README's existing voice and heading levels):
```markdown
## Engines

The agent can run on two engines. Both get the same prompt and the same sandboxed tools (`RepoSandbox`).

- `api` (default): our own loop over the Messages API (`overview_agent/agent.py`). We place the cache breakpoints,
  send the reminder and count everything.
- `agent-sdk`: the Claude Agent SDK runs the loop (`overview_agent/sdk_agent.py`). It needs Python 3.10+. The model
  sees only our five tools (no Claude Code built-ins) and nothing from your machine's Claude Code setup.

Pick one with `--engine api|agent-sdk` (or `OVERVIEW_ENGINE` in `.env`) on `local` and `remote`.

### Benchmark

    python main.py bench /path/to/repo

Runs each engine once locally and three times in E2B (8 runs), scores every overview with a blind LLM judge, and
writes `bench/<timestamp>/summary.md`. Expect about $2 for a medium repo; `--max-usd` (default 3.00) stops it
before it can spend more. If it stops, fix the cause and rerun with `--out bench/<timestamp>` to resume.
```

- [ ] **Step 2: CLAUDE.md**

Under "Where responsibilities live", replace the `agent.py` line and add the new modules:
```markdown
- `overview_agent/task.py`: what both engines tell the model (prompt file, first message, reminder).
- `overview_agent/recorder.py`: `ToolRecorder` (tool-call counts) and `finish_run` (summary, metrics, exit code),
  shared by both engines so their numbers are comparable.
- `overview_agent/agent.py`: the `api` engine: Messages API loop, prompt caching.
- `overview_agent/sdk_agent.py`: the `agent-sdk` engine: Claude Agent SDK, our tools only, machine setup isolated.
- `overview_agent/judge.py`: scores one overview against the repo, blind to the engine.
- `overview_agent/bench.py`: `main.py bench`: the run matrix, spend guard, resume, `summary.md`.
```
In "Commands", add:
```bash
python main.py local <repo_path> --engine agent-sdk   # same run on the Claude Agent SDK
python main.py bench <repo_path>                      # both engines, local + E2B, judged; ~$2
```
In "Conventions", change the cache-breakpoint bullet to start: "Don't remove either cache breakpoint in the `api`
engine's `run()` (`overview_agent/agent.py`)…". Add: "Keep `claude-agent-sdk` pinned to the same version in
`requirements.txt` and `e2b_template.py` (a test checks)."

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/python -m unittest -v 2>&1 | tail -3` → `OK`, and `python main.py --help` lists `bench`.
```bash
git add README.md CLAUDE.md
git commit -m "Docs: engines, the bench command, new modules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Live benchmark and Results (spends about $2)

**Files:**
- Modify: `docs/specs/agent-sdk-engine.md` (Results, Status line)

**Interfaces:**
- Consumes: everything above; `.env` with `ANTHROPIC_API_KEY` and `E2B_API_KEY`.
- Produces: the filled Results section.

- [ ] **Step 1: Rebuild the E2B template**

Run: `.venv/bin/python main.py build-template`
Expected: ends with `Built template overview-agent`.

- [ ] **Step 2: Run the bench**

Run: `.venv/bin/python main.py bench /Users/josue.merino/Projects/terra-agents-backend`
Expected: 8 `== …` lines, then `Wrote …/bench/<timestamp>/summary.md`. The first two runs are the local pilot.
If a run fails, read its `trace.log`, fix the cause in a separate commit, and resume with `--out bench/<timestamp>`.
Never pass `--max-usd` above 3 without asking the user.

- [ ] **Step 3: Spot-check the judge**

Read the two overviews at the end of `summary.md` against the repo's README. Note in the Results whether you agree
with the judge's ordering, and where you don't.

- [ ] **Step 4: Write the Results section**

Replace `Pending.` in `docs/specs/agent-sdk-engine.md` with: the date, the bench folder name, the full table from
`summary.md`, and one short paragraph per question in the Measurement protocol (cost, speed, behavior, quality,
environment), each naming the better engine and by how much. List any failed runs and their causes. Change the
Status line to `implemented and measured`.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/agent-sdk-engine.md
git commit -m "Spec: agent-sdk vs api benchmark results

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
