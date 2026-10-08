# Project Structure Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Implementer for every task: the `refactor` sub-agent** (`.claude/agents/refactor.md`).

**Goal:** Reorganize the flat repo into an `overview_agent/` package with one entry point, `main.py`
(`local` | `remote` | `build-template`), with no change to what the agent does or costs.

**Architecture:** First move the code into the package and pull shared settings into `config.py` (Task 1). Then
replace the three per-script CLIs with `main.py` (Task 2), make the E2B upload list itself from the package
(Task 3), split `tools.py` by responsibility (Task 4), and finally move the docs and update their text (Task 5).
Every task ends with the whole test suite green.

**Tech Stack:** Python 3.9.6 (the project venv), `unittest`, `argparse`, `anthropic`, `e2b`. No new dependencies.

**Spec:** `docs/specs/project-structure.md`

## Global Constraints

- **Workspace:** a git worktree at `.claude/worktrees/project-structure` on branch `refactor/project-structure`,
  created from `main`. All paths below are relative to that worktree.
- **Python:** `PY=/Users/josue.merino/learning/claude-code/overview-agent-dev/.venv/bin/python` (3.9.6, has
  `anthropic` and `e2b`). The worktree has no venv of its own. Every test command is
  `$PY -m unittest -v` (or `$PY -m unittest tests.<module> -v`), run from the worktree root.
- **Python 3.9 syntax:** no `match`, no `X | Y` in runtime annotations. Keep `from __future__ import annotations`
  at the top of every module.
- **Pure move:** function bodies, error and log messages, constants and the `TOOLS` schemas are copied
  **verbatim**. The only allowed edits are imports, the paths named in this plan, the `validate_overview` callback
  (Task 4), and one message (Task 2: `did you run python e2b_template.py?` becomes
  `did you run python main.py build-template?`).
- **Use `git mv`** for every move or rename, so history follows the files.
- **Imports inside the package are absolute:** `from overview_agent.config import trace`, never relative. The
  E2B sandbox runs `python main.py` from `AGENT_DIR`, so `overview_agent` must be importable from the script's
  folder.
- **`overview_agent/__init__.py` contains only a docstring** (no imports).
- **`main.py` never imports `overview_agent.remote` or `overview_agent.e2b_template` at module level.** The sandbox
  image has no `e2b`.
- **Don't touch:** `prompts/overview_agent.md` content, the two cache breakpoints in `run()`, `.gitignore`,
  `.claude/agents/refactor.md`, `requirements.txt`.
- **Commit messages** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The E2B sandbox has no `e2b` package.** `python main.py local …` inside it must not import `e2b`.
   Pinned by `test_local_works_without_the_e2b_package` (Task 2).
2. **macOS litter in the package** (`.DS_Store`, `__pycache__/*.pyc`) would crash the upload, which reads files as
   UTF-8 text. Pinned by `test_only_code_and_prompts_are_uploaded` (Task 3).
3. **Running `main.py` from another folder** (`python ~/…/main.py local ../repo`) must still find the package and
   read `.env` from the project root, not the cwd. Pinned by `test_runs_from_any_working_directory` (Task 2) and
   `test_dotenv_is_read_from_the_project_root` (Task 1).
4. **The prompt must reach the sandbox** at the path `load_instructions()` reads it from
   (`overview_agent/prompts/overview_agent.md`). Pinned by `test_real_package_is_uploaded_with_its_prompt`
   (Task 3).
5. **The evidence-path warnings keep their wording and order** after `validate_overview` leaves the sandbox. Pinned
   by `test_callback_results_become_warnings` (Task 4) plus the existing
   `test_write_overview_warns_on_phantom_evidence`.

---

### Task 1: Package skeleton, `config.py`, move the code and tests

**Files:**
- Move: `agent.py`, `tools.py`, `remote.py`, `e2b_template.py`, `prompts/` → `overview_agent/`
- Move: `test_agent.py`, `test_tools.py`, `test_remote.py` → `tests/`
- Create: `overview_agent/__init__.py`, `overview_agent/config.py`, `tests/__init__.py`, `tests/test_config.py`
- Modify: `overview_agent/agent.py:11-47`, `overview_agent/remote.py:25-28,79`, `overview_agent/e2b_template.py:12-13`
- Modify: `tests/test_agent.py:11`, `tests/test_tools.py:9`, `tests/test_remote.py:16-17,69,350`

**Interfaces:**
- Produces: `overview_agent.config.PROJECT_ROOT: Path` (the repo root), `DEFAULT_MODEL: str`,
  `load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None`, `trace(msg: str) -> None`.

- [ ] **Step 1: Move the files and add the package markers**

```bash
mkdir -p overview_agent tests
git mv agent.py tools.py remote.py e2b_template.py prompts overview_agent/
git mv test_agent.py test_tools.py test_remote.py tests/
printf '"""Overview agent: writes PROJECT_OVERVIEW.md for a repository."""\n' > overview_agent/__init__.py
: > tests/__init__.py
```

- [ ] **Step 2: Write the config tests and point the existing tests at the package**

Create `tests/test_config.py`:

```python
"""Offline tests for the shared settings in overview_agent/config.py."""
from __future__ import annotations

import unittest

from overview_agent import config


class ConfigTests(unittest.TestCase):
    def test_project_root_is_the_repository_root(self):
        self.assertTrue((config.PROJECT_ROOT / "requirements.txt").is_file())
        self.assertTrue((config.PROJECT_ROOT / "overview_agent" / "config.py").is_file())

    def test_dotenv_is_read_from_the_project_root(self):
        self.assertEqual(config.load_dotenv.__defaults__, (config.PROJECT_ROOT / ".env",))


if __name__ == "__main__":
    unittest.main()
```

Edit the test imports:
- `tests/test_agent.py` line 11: `import agent` → `from overview_agent import agent`
- `tests/test_tools.py` line 9: `from tools import …` → `from overview_agent.tools import OVERVIEW_NAME, RepoSandbox, ToolError, is_ignored_dir, is_ignored_name`
- `tests/test_remote.py` lines 16-17: `import remote` / `from tools import OVERVIEW_NAME` →
  ```python
  from overview_agent import config, remote
  from overview_agent.tools import OVERVIEW_NAME
  ```
- `tests/test_remote.py` line 69: `remote.HERE / "overviews" / name` → `config.PROJECT_ROOT / "overviews" / name`
- `tests/test_remote.py` line 350: `import e2b_template` → `from overview_agent import e2b_template`

- [ ] **Step 3: Run the tests to verify they fail**

Run: `$PY -m unittest -v 2>&1 | tail -5`
Expected: FAIL. Errors include `ModuleNotFoundError: No module named 'overview_agent.config'` and
`No module named 'tools'` (raised from `overview_agent/agent.py`).

- [ ] **Step 4: Create `overview_agent/config.py`**

```python
"""Settings shared by every entry point: project paths, the default model, .env loading and the trace log."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-5-5"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing environment variables win; empty values are skipped."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        if value:
            os.environ.setdefault(key.strip(), value)


def trace(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
```

- [ ] **Step 5: Point the package modules at `config` and at each other**

`overview_agent/agent.py`:
- Replace lines 20-23 (`from tools import …`, `DEFAULT_MODEL = …`, `HERE = …`) with:
  ```python
  from overview_agent.config import DEFAULT_MODEL, load_dotenv, trace
  from overview_agent.tools import OVERVIEW_NAME, TIER3_MAX_FILES, TOOLS, RepoSandbox, ToolError
  ```
- Delete the `load_dotenv` function (lines 25-36) and the `trace` function (lines 46-47). Both now live in
  `config.py`.
- In `load_instructions`, replace `path = HERE / "prompts" / "overview_agent.md"` with
  `path = Path(__file__).resolve().parent / "prompts" / "overview_agent.md"`. The error message stays as is.

`overview_agent/remote.py`:
- Replace lines 25-26 with:
  ```python
  from overview_agent.config import DEFAULT_MODEL, PROJECT_ROOT, load_dotenv, trace
  from overview_agent.tools import OVERVIEW_NAME, is_ignored_dir, is_ignored_name
  ```
- Keep `HERE = Path(__file__).resolve().parent` for now (`AGENT_FILES` still reads from it until Task 3).
- In `output_dir`, replace `return HERE / "overviews" / repo_name(source)` with
  `return PROJECT_ROOT / "overviews" / repo_name(source)`.

`overview_agent/e2b_template.py`, lines 12-13:
```python
from overview_agent.config import load_dotenv
from overview_agent.remote import TEMPLATE
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `$PY -m unittest -v 2>&1 | tail -4`
Expected: `Ran 50 tests` … `OK` (48 existing + 2 config tests).

Also run: `git grep -nE "^(from|import) (agent|tools|remote|e2b_template)\b" -- '*.py'`
Expected: no output (no flat imports left).

- [ ] **Step 7: Commit**

```bash
git add -A overview_agent tests
git commit -m "Move code into the overview_agent package and tests into tests/; add config.py

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `main.py`, the single entry point

**Files:**
- Create: `main.py`, `tests/test_main.py`
- Modify: `overview_agent/agent.py` (docstring, imports, delete `main()` and `__main__` block)
- Modify: `overview_agent/remote.py` (docstring, imports, delete `main()` and `__main__` block, line 130 hint)
- Modify: `overview_agent/e2b_template.py` (docstring, `main` → `build`, delete `__main__` block)
- Modify: `tests/test_remote.py` (delete `class MainTests`, update the template-hint assertion)

**Interfaces:**
- Consumes: `overview_agent.config.{DEFAULT_MODEL, load_dotenv, trace}`;
  `overview_agent.agent.run(root, model, max_turns, max_tokens=16000, metrics_json="") -> int`;
  `overview_agent.remote.{parse_source, RemoteError, run_remote(source, args, sandbox_factory=None) -> int}`.
- Produces: `main.main(argv: Optional[List[str]] = None) -> int`, `main.build_parser() -> argparse.ArgumentParser`,
  and `overview_agent.e2b_template.build() -> None` (renamed from `main`).

- [ ] **Step 1: Write the CLI tests**

Create `tests/test_main.py`:

```python
"""Offline tests for the command line in main.py (the agent and E2B are mocked)."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import main
from overview_agent import e2b_template, remote
from overview_agent.config import DEFAULT_MODEL, PROJECT_ROOT


class LocalCommandTests(unittest.TestCase):
    def test_flags_and_env_defaults_reach_agent_run(self):
        env = {"OVERVIEW_MODEL": "claude-opus-5-5", "OVERVIEW_MAX_TOKENS": "8000",
               "OVERVIEW_REPO_PATH": "/tmp/r", "ANTHROPIC_API_KEY": "sk-test"}
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(main.agent, "run", return_value=0) as run:
            code = main.main(["local", "--max-turns", "9", "--metrics-json", "m.json"])
        self.assertEqual(code, 0)
        run.assert_called_once_with("/tmp/r", "claude-opus-5-5", 9, 8000, "m.json")

    def test_missing_api_key_prints_a_note_and_still_runs(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(main.agent, "run", return_value=1) as run, redirect_stderr(err):
            code = main.main(["local", "some/repo"])
        self.assertEqual(code, 1)
        self.assertIn("note: ANTHROPIC_API_KEY is not set", err.getvalue())
        run.assert_called_once_with("some/repo", DEFAULT_MODEL, 25, 16000, "")


class RemoteCommandTests(unittest.TestCase):
    def test_bad_source_is_one_error_line_and_no_sandbox(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), mock.patch.object(remote, "run_remote") as run, \
                redirect_stderr(err):
            code = main.main(["remote", "/definitely/not/here"])
        self.assertEqual(code, 1)
        self.assertIn("error: not a git URL or an existing directory", err.getvalue())
        run.assert_not_called()

    def test_flags_and_env_defaults_reach_run_remote(self):
        env = {"OVERVIEW_MODEL": "claude-opus-5-5", "OVERVIEW_MAX_TURNS": "7"}
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(remote, "run_remote", return_value=0) as run:
            code = main.main(["remote", "github.com/org/repo", "--keep"])
        self.assertEqual(code, 0)
        source, args = run.call_args[0]
        self.assertEqual(source, ("git", "https://github.com/org/repo"))
        self.assertEqual((args.model, args.max_turns, args.max_tokens, args.out, args.keep),
                         ("claude-opus-5-5", 7, 16000, "", True))

    def test_ctrl_c_reports_interrupted_and_exits_130(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), \
                mock.patch.object(remote, "run_remote", side_effect=KeyboardInterrupt), redirect_stderr(err):
            code = main.main(["remote", "github.com/org/repo"])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", err.getvalue())


class BuildTemplateCommandTests(unittest.TestCase):
    def test_build_template_builds_once(self):
        with mock.patch.object(main, "load_dotenv"), mock.patch.object(e2b_template, "build") as build:
            code = main.main(["build-template"])
        self.assertEqual(code, 0)
        build.assert_called_once_with()


class EntryPointTests(unittest.TestCase):
    def test_no_subcommand_is_a_usage_error(self):
        with mock.patch.object(main, "load_dotenv"), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            main.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_local_works_without_the_e2b_package(self):
        # The E2B sandbox image has no e2b package; `main.py local` must not need it.
        code = "import sys; sys.modules['e2b'] = None\nimport main\nmain.main(['local', '--help'])\n"
        result = subprocess.run([sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--metrics-json", result.stdout)

    def test_runs_from_any_working_directory(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            result = subprocess.run([sys.executable, str(PROJECT_ROOT / "main.py"), "local", "--help"],
                                    cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--metrics-json", result.stdout)


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_remote.py`:
- Delete the whole `class MainTests(unittest.TestCase):` block (its 2 tests now live in `test_main.py`).
- In `test_sandbox_creation_failure_points_at_the_template_script`, change
  `self.assertIn("python e2b_template.py", err.getvalue())` to
  `self.assertIn("python main.py build-template", err.getvalue())`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m unittest -v 2>&1 | tail -5`
Expected: FAIL. `tests.test_main` errors with `ModuleNotFoundError: No module named 'main'`, and
`test_sandbox_creation_failure_points_at_the_template_script` fails.

- [ ] **Step 3: Create `main.py`**

```python
#!/usr/bin/env python3
"""Product Overview Agent: writes PROJECT_OVERVIEW.md (what a product does, not how) for a repository.

Usage:
  python main.py local [repo_path] [--model M] [--max-turns N] [--max-tokens N] [--metrics-json PATH]
  python main.py remote <git-url | local-path> [--model M] [--max-turns N] [--max-tokens N] [--out DIR] [--keep]
  python main.py build-template

`local` runs the agent on this machine and writes into the repo. `remote` runs the same agent in an E2B sandbox
and downloads the results into overviews/<repo-name>/. `build-template` builds that sandbox image (once).
"""
from __future__ import annotations

import argparse
import os
import sys

from overview_agent import agent
from overview_agent.config import DEFAULT_MODEL, load_dotenv, trace


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--model", default=env("OVERVIEW_MODEL", DEFAULT_MODEL))
    shared.add_argument("--max-turns", type=int, default=int(env("OVERVIEW_MAX_TURNS", "25")))
    shared.add_argument("--max-tokens", type=int, default=int(env("OVERVIEW_MAX_TOKENS", "16000")))

    parser = argparse.ArgumentParser(description="Write PROJECT_OVERVIEW.md for a repository.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    local = commands.add_parser(
        "local", parents=[shared], help="run the agent on this machine",
        description="Write PROJECT_OVERVIEW.md for a local repository.")
    local.add_argument("repo", nargs="?", default=env("OVERVIEW_REPO_PATH", "."),
                       help="repository root (default: $OVERVIEW_REPO_PATH or the current directory)")
    local.add_argument("--metrics-json", default="", help="also write the run metrics as JSON to this path")
    local.set_defaults(handler=run_local)

    remote = commands.add_parser(
        "remote", parents=[shared], help="run the agent in an E2B sandbox",
        description="Write PROJECT_OVERVIEW.md for a repository, running the agent in an E2B sandbox.")
    remote.add_argument("source", help="git URL (https://..., git@..., github.com/org/repo) or a local folder")
    remote.add_argument("--out", default="", help="results folder (default: overviews/<repo-name>/ in this project)")
    remote.add_argument("--keep", action="store_true", help="leave the sandbox running at the end, to inspect it")
    remote.set_defaults(handler=run_remote)

    template = commands.add_parser("build-template", help="build the E2B sandbox template (once)")
    template.set_defaults(handler=build_template)
    return parser


def run_local(args: argparse.Namespace) -> int:
    env = os.environ.get
    if not (env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN")):
        trace("note: ANTHROPIC_API_KEY is not set (add it to .env); relying on an `ant auth login` profile if one exists")
    return agent.run(args.repo, args.model, args.max_turns, args.max_tokens, args.metrics_json)


def run_remote(args: argparse.Namespace) -> int:
    from overview_agent import remote  # needs the e2b package, which the sandbox image does not have

    try:
        source = remote.parse_source(args.source)
    except remote.RemoteError as e:
        trace("error: %s" % e)
        return 1
    try:
        return remote.run_remote(source, args)
    except KeyboardInterrupt:  # run_remote's `finally` has already killed the sandbox
        trace("interrupted")
        return 130


def build_template(args: argparse.Namespace) -> int:
    from overview_agent import e2b_template  # needs the e2b package

    e2b_template.build()
    return 0


def main(argv=None) -> int:
    load_dotenv()  # before parsing: the option defaults read the environment
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Remove the old CLIs from the package**

`overview_agent/agent.py`:
- Replace the module docstring (lines 1-8, including the `#!/usr/bin/env python3` line) with:
  ```python
  """The agent loop: sends the prompt and tools to Claude, runs each tool call in RepoSandbox, records metrics.

  The instructions come from prompts/overview_agent.md (next to this file). The model explores the repo only
  through the sandboxed tools, which enforce the hard rules. Run it with `python main.py local`.
  """
  ```
- Delete `import argparse` and `import os`.
- Change the config import to `from overview_agent.config import trace`.
- Delete `def main()` and the `if __name__ == "__main__":` block at the end of the file.

`overview_agent/remote.py`:
- Replace the module docstring (lines 1-10, including the shebang) with:
  ```python
  """Run the Product Overview Agent remotely, inside an E2B sandbox (`python main.py remote`).

  The sandbox runs the same agent code as a local run; this module only moves things around. It uploads the
  agent code and the target repo, runs the agent with the API key passed to that one command, and downloads
  PROJECT_OVERVIEW.md and the run metrics into overviews/<repo-name>/. The local folder is never modified.
  Build the sandbox template once first: python main.py build-template
  """
  ```
- Delete `import argparse`.
- Change the config import to `from overview_agent.config import PROJECT_ROOT, trace`.
- In `run_remote`, change `(did you run python e2b_template.py?)` to `(did you run python main.py build-template?)`.
- Delete `def main(argv=None)` and the `if __name__ == "__main__":` block at the end of the file. Keep `_echo`.

`overview_agent/e2b_template.py`:
- Replace the module docstring (lines 1-7, including the shebang) with:
  ```python
  """Build the E2B sandbox template that remote.py runs the agent in.

  Run once, and again only when the agent's dependencies change:  python main.py build-template
  The template is only the environment (Python, git, the anthropic SDK). The agent code is uploaded on every run,
  and no API key is ever baked in.
  """
  ```
- Rename `def main() -> None:` to `def build() -> None:` (body unchanged).
- Delete the `if __name__ == "__main__":` block.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `$PY -m unittest -v 2>&1 | tail -4`
Expected: `Ran 57 tests` … `OK` (50 − 2 moved + 9 in `test_main.py`).

Run: `$PY main.py local --help && $PY main.py remote --help && $PY main.py build-template --help`
Expected: all three exit 0. `local` lists `repo`, `--model`, `--max-turns`, `--max-tokens`, `--metrics-json`;
`remote` lists `source`, `--model`, `--max-turns`, `--max-tokens`, `--out`, `--keep`.

- [ ] **Step 6: Commit**

```bash
git add -A main.py overview_agent tests
git commit -m "Add main.py with local, remote and build-template subcommands; drop the per-script CLIs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: E2B upload lists itself from the package and runs `main.py`

**Files:**
- Modify: `overview_agent/remote.py` (`HERE`, `AGENT_FILES`, `_run_in_sandbox`)
- Modify: `tests/test_remote.py` (fake sandbox command prefix, upload loop, new `AgentFilesTests`)

**Interfaces:**
- Consumes: `overview_agent.config.PROJECT_ROOT`.
- Produces: `overview_agent.remote.agent_files(root: Path = PROJECT_ROOT) -> List[str]` (`"main.py"` first, then
  every `.py`/`.md` under `overview_agent/`, sorted, as POSIX paths relative to `root`). `AGENT_FILES` and
  `HERE` no longer exist.

- [ ] **Step 1: Update the remote tests**

In `tests/test_remote.py`, replace every `"python agent.py"` with `"python main.py local"`. There are 4 places:
`FakeCommands.run`, `FakeSandbox.agent_run`, and two in `test_api_key_goes_only_to_the_agent_command` /
the clone-failure test. Check with `grep -n "python agent.py" tests/test_remote.py`, which must end up empty.

In `test_success_downloads_results_and_kills_the_sandbox`, change `for rel in remote.AGENT_FILES:` to
`for rel in remote.agent_files():`.

In `test_api_key_goes_only_to_the_agent_command`, after `cmd, kw = sbx.agent_run()`, add:
```python
        self.assertTrue(cmd.startswith("python main.py local %s --model " % remote.REPO_DIR), cmd)
```

Add this class before `class TemplateTests`:

```python
class AgentFilesTests(unittest.TestCase):
    def test_real_package_is_uploaded_with_its_prompt(self):
        files = remote.agent_files()
        self.assertEqual(files[0], "main.py")
        for rel in ("overview_agent/agent.py", "overview_agent/config.py",
                    "overview_agent/prompts/overview_agent.md"):
            self.assertIn(rel, files)
        self.assertFalse(any("__pycache__" in rel for rel in files))

    def test_only_code_and_prompts_are_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("overview_agent/a.py", "overview_agent/prompts/p.md", "overview_agent/.DS_Store",
                        "overview_agent/__pycache__/a.cpython-39.pyc"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(b"\x00\xff")
            self.assertEqual(remote.agent_files(root),
                             ["main.py", "overview_agent/a.py", "overview_agent/prompts/p.md"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m unittest tests.test_remote -v 2>&1 | tail -5`
Expected: FAIL with `AttributeError: module 'overview_agent.remote' has no attribute 'agent_files'`, and the
success-path tests fail because the fake sandbox no longer recognizes `python agent.py`.

- [ ] **Step 3: Implement `agent_files()` and the new sandbox command**

In `overview_agent/remote.py`:
- Change `from typing import Tuple` to `from typing import List, Tuple`.
- Delete `HERE = Path(__file__).resolve().parent` and `AGENT_FILES = (…)`. Put these in their place:
  ```python
  AGENT_PACKAGE = "overview_agent"
  AGENT_SUFFIXES = (".py", ".md")  # code and prompts; never caches or OS files like .DS_Store
  ```
- Add after `output_dir`:
  ```python
  def agent_files(root: Path = PROJECT_ROOT) -> List[str]:
      """What the sandbox needs to run the agent: main.py plus the package's code and prompts, relative to `root`."""
      package = sorted(p for p in (root / AGENT_PACKAGE).rglob("*") if p.is_file() and p.suffix in AGENT_SUFFIXES)
      return ["main.py"] + [p.relative_to(root).as_posix() for p in package]
  ```
- In `_run_in_sandbox`, replace the upload loop with:
  ```python
      for rel in agent_files():
          sbx.files.write("%s/%s" % (AGENT_DIR, rel), (PROJECT_ROOT / rel).read_text(encoding="utf-8"))
  ```
- In `_run_in_sandbox`, change the start of the command string from `"python agent.py %s --model …"` to
  `"python main.py local %s --model …"`. Keep the rest of the format string and arguments as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$PY -m unittest -v 2>&1 | tail -4`
Expected: `Ran 59 tests` … `OK`.

Run: `git grep -n "AGENT_FILES\|HERE" -- overview_agent tests main.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add -A overview_agent tests
git commit -m "remote: upload main.py and the whole package (code and prompts only) and run main.py local

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Split `tools.py` by responsibility

**Files:**
- Move: `overview_agent/tools.py` → `overview_agent/sandbox.py` (then cut the other parts out of it)
- Move: `tests/test_tools.py` → `tests/test_sandbox.py`
- Create: `overview_agent/overview_format.py`, `overview_agent/ignore_rules.py`, `overview_agent/tool_schemas.py`,
  `tests/test_overview_format.py`
- Modify: `overview_agent/agent.py`, `overview_agent/remote.py` (imports only), `tests/test_sandbox.py` (docstring,
  imports), `tests/test_remote.py` (import of `OVERVIEW_NAME`)

**Interfaces:**
- Produces:
  - `overview_agent.overview_format`: `OVERVIEW_NAME`, `REQUIRED_HEADINGS`, `MAX_WORDS`, `SOFT_WORDS`,
    `validate_overview(content: str, cited_path_problem: Callable[[str], Optional[str]]) -> Tuple[List[str], List[str]]`
  - `overview_agent.ignore_rules`: `IGNORED_DIRS`, `LOCKFILES`, `IGNORED_FILE_PATTERNS`,
    `is_ignored_dir(name: str) -> bool`, `is_ignored_name(name: str) -> bool`
  - `overview_agent.sandbox`: `ToolError`, `RepoSandbox`, `TIER1_MAX_LINES`, `TIER3_MAX_LINES`, `TIER3_MAX_FILES`,
    `TREE_MAX_ENTRIES`, `MAX_SCAN_FILES`, `MAX_GREP_FILE_BYTES`, `MANIFESTS`, `DOCS_INDEX_STEMS`
  - `overview_agent.tool_schemas`: `TOOLS`
- `overview_agent.tools` no longer exists.

- [ ] **Step 1: Rename the files and point the tests at the new modules**

```bash
git mv overview_agent/tools.py overview_agent/sandbox.py
git mv tests/test_tools.py tests/test_sandbox.py
```

In `tests/test_sandbox.py`:
- Line 1 docstring: `"""Offline tests for the sandbox rules in overview_agent/sandbox.py (no API key needed)."""`
- Replace the `from overview_agent.tools import …` line with:
  ```python
  from overview_agent.ignore_rules import is_ignored_dir, is_ignored_name
  from overview_agent.overview_format import OVERVIEW_NAME
  from overview_agent.sandbox import RepoSandbox, ToolError
  ```

In `tests/test_remote.py`: `from overview_agent.tools import OVERVIEW_NAME` →
`from overview_agent.overview_format import OVERVIEW_NAME`.

Create `tests/test_overview_format.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m unittest -v 2>&1 | tail -5`
Expected: FAIL with `ModuleNotFoundError: No module named 'overview_agent.ignore_rules'` (and
`overview_format`), and `No module named 'overview_agent.tools'` from `agent.py`/`remote.py`.

- [ ] **Step 3: Create `overview_agent/overview_format.py`**

The constants and the validation body are the old `tools.py` code, verbatim. The only changes are
`self._sections` → `_sections`, and the last loop calling `cited_path_problem` instead of `self._resolve`:

```python
"""The PROJECT_OVERVIEW.md template, enforced in code.

Keep REQUIRED_HEADINGS and the word limits in sync with the template in prompts/overview_agent.md.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

OVERVIEW_NAME = "PROJECT_OVERVIEW.md"

REQUIRED_HEADINGS = [
    "In one sentence",
    "Problem & core user",
    "Key features",
    "Main user workflow",
    "Evidence & confidence",
    "Open questions",
]
MAX_WORDS = 700  # the prompt says "under ~600"; 600-700 passes with a warning.
SOFT_WORDS = 600


def validate_overview(
    content: str, cited_path_problem: Callable[[str], Optional[str]]
) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for an overview draft. Errors block the write; warnings are returned with it.

    `cited_path_problem(path)` checks one path cited under 'Evidence & confidence' and returns a warning, or None.
    """
    errors: List[str] = []
    warnings: List[str] = []

    first = next((l for l in content.splitlines() if l.strip()), "")
    if not re.match(r"^# .+: Product Overview\s*$", first):
        errors.append("first line must be '# <Project name>: Product Overview'")

    headings = re.findall(r"^## (.+?)\s*$", content, flags=re.M)
    if headings != REQUIRED_HEADINGS:
        errors.append(
            "the '## ' headings must be exactly, in order: %s (found: %s)"
            % (REQUIRED_HEADINGS, headings)
        )
        return errors, warnings  # section checks below depend on the headings

    sections = _sections(content)
    words = len(content.split())
    if words > MAX_WORDS:
        errors.append("%d words; must be under ~600 (hard limit %d). Cut it down." % (words, MAX_WORDS))
    elif words > SOFT_WORDS:
        warnings.append("%d words; target is under ~600." % words)

    bullets = [l for l in sections["Key features"].splitlines() if re.match(r"^[-*] ", l)]
    if not bullets:
        errors.append("'Key features' needs bullet points ('- feature — what it lets the user do')")
    elif len(bullets) > 7:
        errors.append("'Key features' has %d bullets; maximum is 7" % len(bullets))
    elif len(bullets) < 3:
        warnings.append("'Key features' has %d bullets (expected 3-7); acceptable only if the repo gives no more evidence" % len(bullets))

    steps = [l for l in sections["Main user workflow"].splitlines() if re.match(r"^\d+[.)] ", l)]
    if not steps:
        errors.append("'Main user workflow' must be a numbered list of user steps")
    elif len(steps) < 3:
        warnings.append("'Main user workflow' has %d steps; confirm that is the whole journey" % len(steps))

    if not sections["Open questions"].strip():
        errors.append("'Open questions' must not be empty (write 'None' only if truly nothing is unclear)")
    if len(re.findall(r"\b(?:High|Medium|Low)\b", sections["Evidence & confidence"])) < 3:
        errors.append("'Evidence & confidence' needs a High/Medium/Low rating for each of: problem & core user, key features, main workflow")

    # Grounding: every file cited in backticks under Evidence should exist.
    for token in sorted(set(re.findall(r"`([^`\n]+)`", sections["Evidence & confidence"]))):
        if not re.search(r"[/.]", token) or re.search(r"[*?\[\s]", token):
            continue
        problem = cited_path_problem(token)
        if problem:
            warnings.append(problem)
    return errors, warnings


def _sections(content: str) -> Dict[str, str]:
    parts = re.split(r"^## (.+?)\s*$", content, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
```

- [ ] **Step 4: Create `overview_agent/ignore_rules.py`**

```python
"""What the agent never reads and remote.py never uploads: library folders, lockfiles, generated code, secrets."""
from __future__ import annotations

import fnmatch

from overview_agent.overview_format import OVERVIEW_NAME

IGNORED_DIRS = {
    "node_modules", ".git", "dist", "build", "vendor",
    ".next", ".nuxt", ".venv", "venv", "__pycache__", "coverage", ".cache", ".idea",
    # Library and cache folders of other ecosystems. Generic names (target, out, env, deps, secrets) are left
    # out on purpose: in some repos they are product code.
    "Pods", "Carthage", "bower_components", ".gradle", ".terraform", ".tox", ".mypy_cache", ".pytest_cache",
    ".dart_tool",
}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "composer.lock", "go.sum", "Gemfile.lock",
}
# Generated code, plus secrets: the agent has no business reading credentials, and remote.py never uploads them.
IGNORED_FILE_PATTERNS = [
    "*.min.js", "*.min.css", "*.map", "*.generated.*", "*.pb.go", "*_pb2.py",
    ".env", ".env.*", "*.pem", "*.key", OVERVIEW_NAME,
    "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*", "*.kdbx",
    ".npmrc", ".pypirc", ".netrc", ".envrc", ".git-credentials", "*.tfstate", "*.tfstate.*", "*.tfvars",
    "credentials.json", "service-account*.json",
]


def is_ignored_dir(name: str) -> bool:
    """A folder the agent never enters and remote.py never uploads."""
    return name in IGNORED_DIRS


def is_ignored_name(name: str) -> bool:
    """A file the agent never reads and remote.py never uploads: lockfiles, generated code, secrets."""
    return name in LOCKFILES or any(fnmatch.fnmatch(name, pat) for pat in IGNORED_FILE_PATTERNS)
```

- [ ] **Step 5: Create `overview_agent/tool_schemas.py`**

The 82-line `TOOLS` list is copied byte for byte from the last commit, so it's generated, not retyped:

```bash
{
  printf '"""The tool definitions (JSON schemas) the model sees. sandbox.py enforces the rules behind them."""\n'
  printf 'from __future__ import annotations\n\n'
  git show HEAD:overview_agent/tools.py | sed -n '356,437p'
} > overview_agent/tool_schemas.py
head -4 overview_agent/tool_schemas.py && tail -2 overview_agent/tool_schemas.py
```
Expected: line 4 is `TOOLS = [`, and the file ends with `    },` and `]`.

- [ ] **Step 6: Trim `overview_agent/sandbox.py` to the sandbox**

- Keep the module docstring (lines 1-11) as is: it still describes this module's job.
- Imports: keep `fnmatch`, `os`, `re`, `sys`, `Path`, and `from typing import Callable, Dict, List, Optional`,
  and add:
  ```python
  from overview_agent.ignore_rules import is_ignored_dir, is_ignored_name
  from overview_agent.overview_format import OVERVIEW_NAME, validate_overview
  ```
- Delete what moved: `OVERVIEW_NAME`, `IGNORED_DIRS`, `LOCKFILES`, `IGNORED_FILE_PATTERNS`, `REQUIRED_HEADINGS`,
  `MAX_WORDS`, `SOFT_WORDS`, `is_ignored_dir`, `is_ignored_name`, `_validate_overview`, `_sections`, `TOOLS`.
- Keep: `MANIFESTS`, `DOCS_INDEX_STEMS`, the six Tier/scan limit constants, `ToolError`, `_stderr`, `RepoSandbox`
  (all other methods unchanged).
- In `write_overview`, change `errors, warnings = self._validate_overview(content)` to
  `errors, warnings = validate_overview(content, self._cited_path_problem)`.
- Add this method right after `write_overview`. Its lines are the old grounding check, verbatim:
  ```python
      def _cited_path_problem(self, token: str) -> Optional[str]:
          """A warning if a path cited as evidence is missing or off-limits, else None."""
          try:
              if not self._resolve(token.rstrip("/")).exists():
                  return "cited path not found in repo: `%s` (remove it or fix the name)" % token
          except ToolError:
              return "cited path is off-limits or outside the repo: `%s`" % token
          return None
  ```

- [ ] **Step 7: Update the two importers**

`overview_agent/agent.py`: replace `from overview_agent.tools import …` with:
```python
from overview_agent.overview_format import OVERVIEW_NAME
from overview_agent.sandbox import TIER3_MAX_FILES, RepoSandbox, ToolError
from overview_agent.tool_schemas import TOOLS
```

`overview_agent/remote.py`: replace `from overview_agent.tools import …` with:
```python
from overview_agent.ignore_rules import is_ignored_dir, is_ignored_name
from overview_agent.overview_format import OVERVIEW_NAME
```

- [ ] **Step 8: Run the tests to verify they pass, and check that the move was verbatim**

Run: `$PY -m unittest -v 2>&1 | tail -4`
Expected: `Ran 62 tests` … `OK`.

Run: `git show HEAD:overview_agent/tools.py | sed -n '356,437p' | diff - <(sed -n '/^TOOLS = \[/,$p' overview_agent/tool_schemas.py)`
Expected: no output (the schemas are identical).

Run: `git show HEAD:overview_agent/tools.py | sed -n '23,42p' | diff - <(sed -n '/^IGNORED_DIRS = {/,/^]/p' overview_agent/ignore_rules.py)`
Expected: no output (the ignore lists are identical).

Run: `git grep -n "overview_agent.tools\|from tools\|_validate_overview" -- '*.py'`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add -A overview_agent tests
git commit -m "Split tools.py into ignore_rules, overview_format, sandbox and tool_schemas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Move the docs and update their text

**Files:**
- Move: `specs/*.md` → `docs/specs/`; `docs/superpowers/plans/2026-10-07-e2b-remote-run.md` → `docs/plans/`
- Modify: `README.md`, `CLAUDE.md`, `.env.example`, `docs/specs/e2b-remote-run.md:4,168`,
  `docs/plans/2026-10-07-e2b-remote-run.md:17`, `docs/specs/project-structure.md` (header and Results)

**Interfaces:** none (docs only).

- [ ] **Step 1: Move the docs**

```bash
mkdir -p docs/specs
git mv specs/conversation-caching.md specs/e2b-remote-run.md specs/prompt-separation.md specs/project-structure.md docs/specs/
git mv docs/superpowers/plans/2026-10-07-e2b-remote-run.md docs/plans/
rmdir specs docs/superpowers/plans docs/superpowers
```

- [ ] **Step 2: Fix the cross-links**

- `docs/specs/e2b-remote-run.md` line 4: `docs/superpowers/plans/2026-10-07-e2b-remote-run.md` →
  `docs/plans/2026-10-07-e2b-remote-run.md`
- `docs/specs/e2b-remote-run.md` line 168: `specs/conversation-caching.md` → `docs/specs/conversation-caching.md`
- `docs/plans/2026-10-07-e2b-remote-run.md` line 17: `**Spec:** \`specs/e2b-remote-run.md\`` →
  `**Spec:** \`docs/specs/e2b-remote-run.md\``. The rest of that old plan is a historical record: leave it.
- `docs/specs/project-structure.md` line 3: append ` · Plan: \`docs/plans/2026-10-08-project-structure.md\``, and
  change `Status: approved design, not implemented` to `Status: implemented`.
- Leave `docs/specs/conversation-caching.md` lines 6 and 74 as they are (historical references to another branch).

- [ ] **Step 3: Update `README.md`**

| Line | From | To |
|---|---|---|
| 3-4 | `` Instructions live in `prompts/overview_agent.md`; `tools.py` enforces its hard rules in code `` | `` Instructions live in `overview_agent/prompts/overview_agent.md`; `overview_agent/sandbox.py` enforces its hard rules in code `` |
| 7 | `` (`agent.py` + `tools.py`) `` | `` (the `overview_agent/` package) `` |
| 10 | `` Local run (`agent.py`) `` / `` Remote run (`remote.py`) `` | `` Local run (`main.py local`) `` / `` Remote run (`main.py remote`) `` |
| 20 | `` `specs/e2b-remote-run.md` `` | `` `docs/specs/e2b-remote-run.md` `` |
| 46 | `` when `agent.py` gets no path `` | `` when `main.py local` gets no path `` |
| 51-53 | `python agent.py ` | `python main.py local ` |
| 69 | `python e2b_template.py` | `python main.py build-template` |
| 70-74 | `python remote.py ` | `python main.py remote ` |
| 80 | `` Uploads `agent.py`, `tools.py` and the prompt `` | `` Uploads `main.py` and the `overview_agent/` package (code and prompt) `` |
| 83 | `` Runs `python agent.py` in the sandbox `` | `` Runs `python main.py local` in the sandbox `` |
| 102, 108 | `` `tools.py` `` | `` `overview_agent/ignore_rules.py` `` |
| 116 | `did you run python e2b_template.py?` | `did you run python main.py build-template?` |

Also re-align the `#` comments in the two command blocks, which shift because the commands get longer. Update any
other command line that `grep -n "python \(agent\|remote\|e2b_template\)\.py\|tools\.py\|specs/" README.md` still
shows (except `docs/specs/`). The test command line (`python3 -m unittest -v`) stays as is.

- [ ] **Step 4: Update `.env.example`**

- Line 11: `(python e2b_template.py / python remote.py)` → `(python main.py build-template / remote)`
- Line 15: `defaults for both agent.py and remote.py` → `defaults for both main.py local and remote`
- Line 20: `(agent.py with no repo argument)` → `(main.py local with no repo argument)`

Keep each line's trailing `-----` padding at the same total width as before.

- [ ] **Step 5: Update `CLAUDE.md`**

Replace the first paragraph (under `# Overview Agent (dev guide)`) with:
```markdown
A Python CLI agent: `python main.py local` runs a Claude API tool-use loop (`overview_agent/agent.py`) with
sandboxed tools (`overview_agent/sandbox.py`) and writes `PROJECT_OVERVIEW.md` (what a product does, not how)
for some *other* repository.
```

In `## The runtime prompt is not for you`: `prompts/overview_agent.md` → `overview_agent/prompts/overview_agent.md`.

Replace the whole `## Where responsibilities live` list and the sync paragraph after it with:
```markdown
## Where responsibilities live
- `main.py`: the only entry point (`local`, `remote`, `build-template`): argparse, env defaults, exit codes.
  It imports `remote` and `e2b_template` lazily, because the sandbox image has no `e2b`.
- `overview_agent/prompts/overview_agent.md`: what the model is told (goals, tiers, template, self-checks). Keep it
  short. No shell examples: the tool descriptions already explain usage.
- `overview_agent/config.py`: `PROJECT_ROOT`, `DEFAULT_MODEL`, `.env` loading, `trace`.
- `overview_agent/overview_format.py`: the overview template in code (`OVERVIEW_NAME`, `REQUIRED_HEADINGS`,
  `MAX_WORDS`/`SOFT_WORDS`, `validate_overview`).
- `overview_agent/ignore_rules.py`: ignore lists and secret patterns, shared by the sandbox and `remote.py`'s
  upload filter.
- `overview_agent/sandbox.py`: `RepoSandbox`, the rules enforced in code (Tier 1/3 access, Tier 3 budget, single
  writable file).
- `overview_agent/tool_schemas.py`: the `TOOLS` schemas the model sees.
- `overview_agent/agent.py`: API loop, prompt caching, metrics.
- `overview_agent/remote.py`: E2B orchestration only (sandbox, clone or upload, run `main.py local`, download
  results). No agent logic.
- `overview_agent/e2b_template.py`: the sandbox image (Python, git, anthropic). No secrets; `remote.py` uploads
  `main.py` and the package on every run.

Keep these in sync. If you change the template headings or word limit in the prompt, update `REQUIRED_HEADINGS`
and `MAX_WORDS`/`SOFT_WORDS` in `overview_format.py`, and the reverse.
```

Replace the `## Commands` code block with:
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                    # ANTHROPIC_API_KEY, model, limits, default repo
python main.py local <repo_path>        # live run, needs an API key
python main.py build-template           # once: build the E2B sandbox template (needs E2B_API_KEY)
python main.py remote <git-url|path>    # live run in E2B; results in overviews/<repo-name>/
python3 -m unittest -v                  # offline, tests/: sandbox, format, loop, CLI, E2B (fake sandbox)
```

In `## Conventions`:
- `` Don't remove either cache breakpoint in `run()` `` → `` Don't remove either cache breakpoint in `run()` (`overview_agent/agent.py`) ``
- `` `test_agent.py` asserts both. `` → `` `tests/test_agent.py` asserts both. ``
- `` get a spec in `specs/` `` → `` get a spec in `docs/specs/` ``. Add after that bullet:
  `` - Implementation plans live in `docs/plans/`. ``

- [ ] **Step 6: Look for stale references**

Run:
```bash
git grep -nE "python (agent|remote|e2b_template)\.py|\btools\.py\b|\]\(specs/|\`specs/|docs/superpowers" -- . ':!docs/specs' ':!docs/plans'
```
Expected: no output.

- [ ] **Step 7: Full verification and the spec's Results**

Run: `$PY -m unittest -v 2>&1 | tail -4`
Expected: `Ran 62 tests` … `OK`.

Run: `$PY main.py --help; $PY main.py local --help; $PY main.py remote --help`
Expected: `--help` lists the three commands; `local` and `remote` show the options listed in the spec's CLI table,
with the same defaults.

Replace the `## Results` paragraph of `docs/specs/project-structure.md` with the real numbers:
```markdown
## Results (2026-10-08)
- Tests: 48 before (`233e296`), 62 after, all passing offline (`python3 -m unittest -v`). The 14 new tests cover
  config paths, the CLI subcommands, running without `e2b`, the upload list, and `validate_overview`.
- CLI: `main.py local --help` and `main.py remote --help` list the same options and defaults as the old
  `agent.py` / `remote.py` scripts.
- Live runs: not repeated. The agent code, prompt and tool schemas are unchanged (the schemas are byte-identical).
```
If a live run was done, replace the last bullet with its result.

- [ ] **Step 8: Commit**

```bash
git add -A docs README.md CLAUDE.md .env.example
git commit -m "Docs: move specs and plans under docs/, document main.py and the package layout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
