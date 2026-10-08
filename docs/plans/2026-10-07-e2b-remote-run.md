# E2B Remote Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `python remote.py <git-url | local-path>`, which runs the existing overview agent unchanged inside
an E2B sandbox and downloads `PROJECT_OVERVIEW.md` plus `metrics.json` into `overviews/<repo-name>/`.

**Architecture:** `remote.py` is a thin orchestrator. Its pure functions (parsing the source, naming the output
folder, building a filtered tarball) are tested offline. `run_remote()` does the E2B I/O and takes a sandbox
factory, so tests can inject a fake. `e2b_template.py` builds the sandbox image once (Python, git, anthropic). The
agent code is uploaded on every run. The ignore rules in `tools.py` become public functions, so the agent's read
rules and the upload filter are the same list, which is also expanded with always-safe secret and library
patterns.

**Tech Stack:** Python 3.9 (project venv), `anthropic` (existing), `e2b==2.10.2` (sandbox SDK), `unittest`.

**Spec:** `docs/specs/e2b-remote-run.md`

## Global Constraints

- Python **3.9** compatible (the project venv is 3.9.6): no `X | Y` unions at runtime, no `match`. Keep
  `from __future__ import annotations` at the top of every module.
- Pin `e2b==2.10.2`. It's the version pip resolves on Python 3.9, and every signature in this plan was checked
  against it.
- Template alias `overview-agent`. Sandbox timeout **15 minutes**. Agent command timeout **14 minutes**, set
  explicitly because E2B's `commands.run` default is `timeout=60` seconds.
- Sandbox paths: code in `/home/user/agent/`, repo in `/home/user/repo`, metrics at `/home/user/metrics.json`.
- Results default to `<project>/overviews/<repo-name>/` (git-ignored), resolved against the project folder, not
  the cwd. `--out DIR` overrides. **The local source folder is never modified.**
- `ANTHROPIC_API_KEY` is passed **only** in the `envs` of the single `python agent.py …` command. It never goes in
  the template or any other command.
- **Unchanged:** `agent.py`, `prompts/overview_agent.md`, the `TOOLS` schemas, and both cache breakpoints in
  `run()`.
- Tests stay offline: no network, no API keys.
- Never commit `.env`.

## Clarifications of the spec (decided while planning)

1. **`.git*` folders:** the agent can *read* `.github/…` (`_walk` hides `.git*` folders only from listings, and
   `_resolve` blocks only `IGNORED_DIRS`). "Exclude exactly what the agent can't read" therefore means
   `is_ignored_dir()` = `IGNORED_DIRS`, which already contains `.git`. `.github/` is uploaded, `.git/` is not.
2. **`parse_source` order:** URL prefixes (`https://`, `http://`, `git@`, `ssh://`) → git. Then an existing
   directory → local, so a local folder named `x.git` is local. Then a scheme-less `host.tld/path` such as
   `github.com/org/repo` → git, with `https://` prepended. Anything else is an error.
3. **Template not found:** E2B 2.10.2 raises a generic `SandboxException` when creation fails, with no specific
   "template missing" type. So *any* creation failure prints the hint `run python e2b_template.py` plus the
   SDK's message.

## Review Focus

1. **Scheme-less GitHub URL** (`github.com/org/repo`, the way people copy it from the address bar) should clone
   over https, not fail as "not a directory". Covered in Task 2 (`test_scheme_less_host_path_becomes_https`).
2. **A private or misspelled repo URL** must fail fast with git's own message, not hang on a hidden credentials
   prompt inside the sandbox. `GIT_TERMINAL_PROMPT=0` goes in the clone's envs. Covered in Task 3
   (`test_clone_failure_reports_git_error_and_skips_agent`).
3. **A stale result from a previous run** in `overviews/<name>/` must not look like this run's result when the
   agent fails. Old files are deleted before downloading. Covered in Task 3
   (`test_agent_failure_passes_exit_code_keeps_metrics_and_clears_stale_overview`).
4. **A local folder whose own name is ignored** (`../build`, `../dist`) or given as `.` must still upload its
   content and get its real name. Covered in Task 2 (tarball root is always `repo/`; `test_dot_resolves_to_real_folder`).
5. **`kill()` failing after a successful run** (a network blip) must not turn success into a crash. The run warns
   and keeps its exit code. Covered in Task 3 (`test_kill_failure_does_not_change_the_result`).

## Before you start

Work in a git worktree (superpowers:using-git-worktrees). `.venv/` is git-ignored, so a fresh worktree has none.
Create one there:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v        # baseline: all existing tests pass
```

## File Structure

| File | Responsibility |
|---|---|
| `tools.py` (modify) | Public `is_ignored_dir()` / `is_ignored_name()`, plus the expanded ignore lists. `RepoSandbox` uses them. |
| `remote.py` (create) | CLI and E2B orchestration: `parse_source`, `repo_name`, `output_dir`, `make_tarball`, `run_remote`, `main`. No agent logic. |
| `e2b_template.py` (create) | `template()` builder and a `main()` that builds alias `overview-agent`. |
| `test_tools.py` (modify) | Tests for the expanded lists and the public helpers. |
| `test_remote.py` (create) | Offline tests for `remote.py` (fake sandbox) and the template definition. |
| `requirements.txt`, `.env.example`, `.gitignore`, `README.md`, `CLAUDE.md` (modify) | Dependency, key placeholder, ignore `overviews/`, docs. |
| `specs/e2b-remote-run.md` (modify, Task 5) | Status and Results. |

---

### Task 1: Shared, expanded ignore rules in `tools.py`

**Files:**
- Modify: `tools.py:1-10` (docstring), `tools.py:22-34` (lists), `tools.py:77-109` (`RepoSandbox` path helpers)
- Test: `test_tools.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `tools.is_ignored_dir(name: str) -> bool` (True iff `name in IGNORED_DIRS`) and
  `tools.is_ignored_name(name: str) -> bool` (True iff `name` is a lockfile or matches `IGNORED_FILE_PATTERNS`).
  `RepoSandbox._name_ignored` is removed.

- [ ] **Step 1: Write the failing tests**

Change the import line at the top of `test_tools.py` to:

```python
from tools import OVERVIEW_NAME, RepoSandbox, ToolError, is_ignored_dir, is_ignored_name
```

Then add this class just above the final `if __name__ == "__main__":` block:

```python
class ExpandedIgnoreTests(unittest.TestCase):
    """Secrets and library folders that are never product code, in any repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for rel in ("README.md", "config/credentials.json", "infra/prod.tfstate", "certs/push.p12",
                    "ios/Pods/Lib/lib.swift", "src/secrets/vault.py"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        self.sb = RepoSandbox(str(self.root), log=lambda m: None)

    def test_new_secrets_and_library_folders_are_refused(self):
        for bad in ("config/credentials.json", "infra/prod.tfstate", "certs/push.p12", "ios/Pods/Lib/lib.swift"):
            with self.assertRaises(ToolError, msg=bad):
                self.sb.read_file(bad, 3, 1, "x")

    def test_new_ignores_are_hidden_but_generic_names_stay_visible(self):
        tree = self.sb.list_tree(".", 3)
        self.assertNotIn("Pods", tree)
        self.assertNotIn("credentials.json", tree)
        self.assertIn("vault.py", tree)  # `secrets/` can be product code, so it is not built in

    def test_public_helpers(self):
        for name in ("node_modules", ".git", "Pods", ".terraform"):
            self.assertTrue(is_ignored_dir(name), name)
        for name in ("secrets", "src", "target", ".github"):
            self.assertFalse(is_ignored_dir(name), name)
        for name in (".env", ".env.local", "yarn.lock", "app.min.js", "id_rsa", "id_rsa.pub", "prod.tfvars",
                     "service-account-prod.json", "credentials.json", ".npmrc"):
            self.assertTrue(is_ignored_name(name), name)
        for name in ("README.md", "secrets-policy.md", "credentials.py", "package.json"):
            self.assertFalse(is_ignored_name(name), name)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest test_tools -v`
Expected: ERROR, `ImportError: cannot import name 'is_ignored_dir' from 'tools'`

- [ ] **Step 3: Expand the lists and add the helpers**

In `tools.py`, replace the `IGNORED_DIRS` set and the `IGNORED_FILE_PATTERNS` list with:

```python
IGNORED_DIRS = {
    "node_modules", ".git", "dist", "build", "vendor",
    ".next", ".nuxt", ".venv", "venv", "__pycache__", "coverage", ".cache", ".idea",
    # Library and cache folders of other ecosystems. Generic names (target, out, env, deps, secrets) are left
    # out on purpose: in some repos they are product code.
    "Pods", "Carthage", "bower_components", ".gradle", ".terraform", ".tox", ".mypy_cache", ".pytest_cache",
    ".dart_tool",
}
```

```python
# Generated code, plus secrets: the agent has no business reading credentials, and remote.py never uploads them.
IGNORED_FILE_PATTERNS = [
    "*.min.js", "*.min.css", "*.map", "*.generated.*", "*.pb.go", "*_pb2.py",
    ".env", ".env.*", "*.pem", "*.key", OVERVIEW_NAME,
    "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*", "*.kdbx",
    ".npmrc", ".pypirc", ".netrc", "*.tfstate", "*.tfstate.*", "*.tfvars",
    "credentials.json", "service-account*.json",
]
```

Right after the `SOFT_WORDS = 600` line (before `class ToolError`), add:

```python
def is_ignored_dir(name: str) -> bool:
    """A folder the agent never enters and remote.py never uploads."""
    return name in IGNORED_DIRS


def is_ignored_name(name: str) -> bool:
    """A file the agent never reads and remote.py never uploads: lockfiles, generated code, secrets."""
    return name in LOCKFILES or any(fnmatch.fnmatch(name, pat) for pat in IGNORED_FILE_PATTERNS)
```

- [ ] **Step 4: Make `RepoSandbox` use them**

In `RepoSandbox`, delete the whole `_name_ignored` staticmethod (the `@staticmethod` line through
`return (... )`), and replace `_rel_ignored` and the two filter lines in `_walk` so the block reads:

```python
    def _rel_ignored(self, rel: Path) -> bool:
        parts = rel.parts
        if any(is_ignored_dir(p) for p in parts):
            return True
        return bool(parts) and is_ignored_name(parts[-1])
```

```python
    def _walk(self, start: Path):
        """Yield (dirpath, dirnames, filenames) with ignored entries pruned, in stable order."""
        for dirpath, dirnames, filenames in os.walk(str(start)):
            dirnames[:] = sorted(
                d for d in dirnames if not is_ignored_dir(d) and not d.startswith(".git")
            )
            filenames = sorted(f for f in filenames if not is_ignored_name(f))
            yield Path(dirpath), dirnames, filenames
```

(`_rel_ignored` used to check the parents and then the last part against `IGNORED_DIRS` separately.
`any(... for p in parts)` is the same check.)

Update the module docstring's first rule line to:

```
  * nothing is read inside node_modules/.git/dist/build/vendor (and other library folders), lockfiles,
    generated code or secrets (.env, keys, keystores, credentials files)
```

- [ ] **Step 5: Run all the tests to verify they pass**

Run: `.venv/bin/python -m unittest -v`
Expected: every test passes, including the existing `SandboxTests` (unchanged behavior) and the 3 new ones.
`grep -n "_name_ignored" tools.py` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add tools.py test_tools.py
git commit -m "Share ignore rules as public helpers and add always-safe secret/library patterns"
```

---

### Task 2: `remote.py` pure functions and the `e2b` dependency

**Files:**
- Modify: `requirements.txt`
- Create: `remote.py`
- Create: `test_remote.py`

**Interfaces:**
- Consumes: `tools.is_ignored_dir`, `tools.is_ignored_name`, `tools.OVERVIEW_NAME` (Task 1);
  `agent.DEFAULT_MODEL`, `agent.load_dotenv`, `agent.trace` (existing).
- Produces (all in `remote.py`):
  - Constants `HERE: Path`, `TEMPLATE = "overview-agent"`, `SANDBOX_TIMEOUT = 900`, `AGENT_TIMEOUT = 840`,
    `CLONE_TIMEOUT = 300`, `HOME = "/home/user"`, `AGENT_DIR`, `REPO_DIR`, `TARBALL`, `METRICS`, `AGENT_FILES`.
  - `class RemoteError(Exception)`
  - `parse_source(arg: str) -> Tuple[str, str]`, which returns `("git", url)` or `("local", absolute_path_str)`
  - `repo_name(source: Tuple[str, str]) -> str`
  - `output_dir(source: Tuple[str, str], out: str = "") -> Path`
  - `make_tarball(path: str) -> bytes`, a gzipped tar whose single top folder is `repo/`

- [ ] **Step 1: Add and install the dependency**

`requirements.txt` becomes:

```
anthropic
e2b==2.10.2
```

Run: `.venv/bin/pip install -r requirements.txt`
Expected: `Successfully installed e2b-2.10.2 …` (or "already satisfied").

- [ ] **Step 2: Write the failing tests**

Create `test_remote.py`:

```python
"""Offline tests for remote.py (fake sandbox, no E2B or Anthropic key needed)."""
from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

import remote


def tar_members(data: bytes):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


class SourceTests(unittest.TestCase):
    def test_urls_are_git(self):
        for url in ("https://github.com/org/repo", "http://host/x.git", "git@github.com:org/repo.git",
                    "ssh://git@host/r"):
            self.assertEqual(remote.parse_source(url), ("git", url))

    def test_scheme_less_host_path_becomes_https(self):
        self.assertEqual(remote.parse_source("github.com/org/repo"), ("git", "https://github.com/org/repo"))

    def test_existing_directory_is_local_even_if_named_like_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "thing.git"
            d.mkdir()
            self.assertEqual(remote.parse_source(str(d)), ("local", str(d.resolve())))

    def test_dot_resolves_to_real_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                source = remote.parse_source(".")
            finally:
                os.chdir(cwd)
            self.assertEqual(source, ("local", str(Path(tmp).resolve())))
            self.assertEqual(remote.repo_name(source), Path(tmp).resolve().name)

    def test_not_a_url_nor_a_directory_is_an_error(self):
        for bad in ("/definitely/not/here", "../nope", "foo.git"):
            with self.assertRaises(remote.RemoteError, msg=bad):
                remote.parse_source(bad)


class OutputDirTests(unittest.TestCase):
    def test_default_is_overviews_slash_repo_name(self):
        cases = {
            ("git", "https://github.com/org/repo.git"): "repo",
            ("git", "https://github.com/org/repo/"): "repo",
            ("git", "git@github.com:org/my-app.git"): "my-app",
            ("local", "/home/me/dayNight"): "dayNight",
        }
        for source, name in cases.items():
            self.assertEqual(remote.output_dir(source), remote.HERE / "overviews" / name, source)

    def test_explicit_out_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(remote.output_dir(("git", "https://h/x"), tmp), Path(tmp).resolve())


class TarballTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        (base / "outside.txt").write_text("secret")
        self.root = base / "build"  # the folder's own name is an ignored dir; its content must still upload
        for rel in ("README.md", "src/app.py", "src/secrets/vault.py", ".github/workflows/ci.yml",
                    ".env", "certs/id.pem", "config/credentials.json", "infra/prod.tfstate",
                    "node_modules/dep/index.js", ".git/HEAD", "ios/Pods/Lib/lib.swift", "yarn.lock"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        os.symlink(str(base / "outside.txt"), str(self.root / "link.txt"))
        self.data = remote.make_tarball(str(self.root))
        self.members = tar_members(self.data)

    def test_keeps_product_files_under_repo(self):
        for rel in ("README.md", "src/app.py", "src/secrets/vault.py", ".github/workflows/ci.yml"):
            self.assertIn("repo/" + rel, self.members)

    def test_drops_what_the_agent_may_not_read(self):
        names = "\n".join(self.members)
        for hidden in (".env", "id.pem", "credentials.json", "prod.tfstate", "node_modules", "Pods", "yarn.lock"):
            self.assertNotIn(hidden, names, hidden)
        self.assertNotIn("repo/.git", self.members)

    def test_symlink_is_stored_as_link_not_followed(self):
        self.assertTrue(self.members["repo/link.txt"].issym())
        with tarfile.open(fileobj=io.BytesIO(self.data), mode="r:gz") as tar:
            contents = [tar.extractfile(m).read() for m in tar.getmembers() if m.isreg()]
        self.assertNotIn(b"secret", contents)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest test_remote -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'remote'`

- [ ] **Step 4: Create `remote.py` with the constants and pure functions**

```python
#!/usr/bin/env python3
"""Run the Product Overview Agent remotely, inside an E2B sandbox.

Usage:  python remote.py <git-url | local-path> [--model M] [--max-turns N] [--max-tokens N] [--out DIR] [--keep]

The sandbox runs the same agent.py/tools.py as a local run; this script only moves things around. It uploads the
agent code and the target repo, runs the agent with the API key passed to that one command, and downloads
PROJECT_OVERVIEW.md and the run metrics into overviews/<repo-name>/. The local folder is never modified.
Build the sandbox template once first: python e2b_template.py
"""
from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path
from typing import Tuple

from tools import is_ignored_dir, is_ignored_name

HERE = Path(__file__).resolve().parent
TEMPLATE = "overview-agent"
SANDBOX_TIMEOUT = 15 * 60  # seconds, the whole sandbox
AGENT_TIMEOUT = 14 * 60    # seconds; E2B's per-command default (60s) is far shorter than an agent run
CLONE_TIMEOUT = 5 * 60
HOME = "/home/user"
AGENT_DIR = HOME + "/agent"
REPO_DIR = HOME + "/repo"
TARBALL = HOME + "/repo.tar.gz"
METRICS = HOME + "/metrics.json"
AGENT_FILES = ("agent.py", "tools.py", "prompts/overview_agent.md")

URL_PREFIXES = ("https://", "http://", "git@", "ssh://")
HOST_PATH = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/[^/]")  # github.com/org/repo, without a scheme


class RemoteError(Exception):
    """A problem with the command-line input, reported as one `error:` line."""


def parse_source(arg: str) -> Tuple[str, str]:
    """('git', url) or ('local', absolute path). An existing directory wins over a URL-looking name."""
    if arg.startswith(URL_PREFIXES):
        return "git", arg
    path = Path(arg).expanduser()
    if path.is_dir():
        return "local", str(path.resolve())
    if HOST_PATH.match(arg):
        return "git", "https://" + arg
    raise RemoteError("not a git URL or an existing directory: %s" % arg)


def repo_name(source: Tuple[str, str]) -> str:
    kind, where = source
    if kind == "local":
        return Path(where).name or "repo"
    name = re.split(r"[/:]", where.rstrip("/"))[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


def output_dir(source: Tuple[str, str], out: str = "") -> Path:
    """Where results land: `out` if given, else overviews/<repo-name>/ in this project (not the cwd)."""
    if out:
        return Path(out).expanduser().resolve()
    return HERE / "overviews" / repo_name(source)


def make_tarball(path: str) -> bytes:
    """A .tar.gz of `path` under the top folder `repo/`, minus everything the agent may not read.

    Ignored folders are pruned whole. Symlinks are stored as links and never followed.
    """
    def keep(info: tarfile.TarInfo):
        name = Path(info.name).name
        if info.isdir() and is_ignored_dir(name):
            return None
        if not info.isdir() and is_ignored_name(name):
            return None
        return info

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(path, arcname="repo", filter=keep)
    return buf.getvalue()
```

(The top folder is always `repo`, so a source folder named `build` isn't dropped. `tarfile.add` stops recursing
into a folder when `filter` returns `None`, and with the default `dereference=False` a symlink becomes a link
entry, never its target's content.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest test_remote -v`
Expected: 10 tests, all pass.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt remote.py test_remote.py
git commit -m "remote.py: parse the source, name the output folder, build a filtered tarball"
```

---

### Task 3: `run_remote()`, one sandbox run

**Files:**
- Modify: `remote.py` (imports and new functions)
- Modify: `test_remote.py` (fake sandbox and `RunRemoteTests`)

**Interfaces:**
- Consumes: everything Task 2 produces; `e2b.Sandbox.create(template: str, timeout: int)`;
  `sbx.files.write(path, data: str | bytes)` (creates parent dirs); `sbx.files.exists(path) -> bool`;
  `sbx.files.read(path, format="bytes")`; `sbx.commands.run(cmd, envs=, cwd=, on_stderr=, timeout=)`, which
  returns an object with `.stdout` and raises `e2b.CommandExitException` (attrs `.exit_code`, `.stdout`,
  `.stderr`) on a non-zero exit and `e2b.TimeoutException` on timeout; `sbx.kill()`; `sbx.sandbox_id`.
- Produces: `run_remote(source: Tuple[str, str], args, sandbox_factory=None) -> int`. `args` needs `.model`,
  `.max_turns`, `.max_tokens`, `.out`, `.keep`. Returns the agent's exit code, or 1 on any failure around it.

- [ ] **Step 1: Write the failing tests**

Add to the imports of `test_remote.py`:

```python
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from e2b import CommandExitException, SandboxException, TimeoutException

from tools import OVERVIEW_NAME
```

Add these classes above `if __name__ == "__main__":`:

```python
class FakeFiles:
    def __init__(self):
        self.store = {}

    def write(self, path, data):
        self.store[path] = data

    def exists(self, path):
        return path in self.store

    def read(self, path, format="text"):
        data = self.store[path]
        return data.encode() if format == "bytes" and isinstance(data, str) else data


class FakeCommands:
    def __init__(self, sbx, agent_exit=0, clone_fails=False, agent_times_out=False):
        self.sbx, self.agent_exit, self.clone_fails, self.agent_times_out = sbx, agent_exit, clone_fails, agent_times_out
        self.runs = []

    def run(self, cmd, **kw):
        self.runs.append((cmd, kw))
        if cmd.startswith("git clone") and self.clone_fails:
            raise CommandExitException(stderr="fatal: repository not found", stdout="", exit_code=128, error=None)
        if cmd.startswith("python agent.py"):
            if self.agent_times_out:
                raise TimeoutException("command timed out")
            kw["on_stderr"]("[turn 1] reading README\n")
            self.sbx.files.store[remote.METRICS] = '{"turns": 1}\n'
            if self.agent_exit:
                raise CommandExitException(stderr="", stdout="no overview", exit_code=self.agent_exit, error=None)
            self.sbx.files.store[remote.REPO_DIR + "/" + OVERVIEW_NAME] = "# X: Product Overview\n"
            return SimpleNamespace(stdout="agent summary\n", stderr="", exit_code=0)
        return SimpleNamespace(stdout="", stderr="", exit_code=0)


class FakeSandbox:
    def __init__(self, kill_fails=False, **behavior):
        self.sandbox_id = "sbx-test"
        self.files = FakeFiles()
        self.commands = FakeCommands(self, **behavior)
        self.kill_fails = kill_fails
        self.killed = False

    def kill(self):
        self.killed = True
        if self.kill_fails:
            raise SandboxException("network blip")

    def agent_run(self):
        return next((cmd, kw) for cmd, kw in self.commands.runs if cmd.startswith("python agent.py"))


KEYS = {"E2B_API_KEY": "e2b_test", "ANTHROPIC_API_KEY": "sk-test"}


class RunRemoteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "out"
        self.args = SimpleNamespace(model="claude-sonnet-5-5", max_turns=25, max_tokens=16000,
                                    out=str(self.out), keep=False)
        self.factory_calls = []

    def run_with(self, sbx, source=("git", "https://github.com/org/repo"), env=KEYS):
        def factory(**kwargs):
            self.factory_calls.append(kwargs)
            return sbx
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = remote.run_remote(source, self.args, sandbox_factory=factory)
        return code, out.getvalue(), err.getvalue()

    def test_success_downloads_results_and_kills_the_sandbox(self):
        sbx = FakeSandbox()
        code, out, err = self.run_with(sbx)
        self.assertEqual(code, 0)
        self.assertEqual(self.factory_calls, [{"template": remote.TEMPLATE, "timeout": remote.SANDBOX_TIMEOUT}])
        for rel in remote.AGENT_FILES:
            self.assertIn(remote.AGENT_DIR + "/" + rel, sbx.files.store)
        self.assertEqual((self.out / OVERVIEW_NAME).read_text(), "# X: Product Overview\n")
        self.assertEqual((self.out / "metrics.json").read_text(), '{"turns": 1}\n')
        self.assertIn("agent summary", out)
        self.assertIn("[turn 1] reading README", err)  # trace streamed live
        self.assertTrue(sbx.killed)

    def test_api_key_goes_only_to_the_agent_command(self):
        sbx = FakeSandbox()
        self.run_with(sbx)
        cmd, kw = sbx.agent_run()
        self.assertEqual(kw["envs"], {"ANTHROPIC_API_KEY": "sk-test"})
        self.assertEqual(kw["cwd"], remote.AGENT_DIR)
        self.assertEqual(kw["timeout"], remote.AGENT_TIMEOUT)
        self.assertIn("--model claude-sonnet-5-5 --max-turns 25 --max-tokens 16000", cmd)
        self.assertIn("--metrics-json " + remote.METRICS, cmd)
        others = [kw for c, kw in sbx.commands.runs if not c.startswith("python agent.py")]
        self.assertTrue(all("ANTHROPIC_API_KEY" not in (kw.get("envs") or {}) for kw in others))
        self.assertTrue(all("sk-test" not in str(v) for v in sbx.files.store.values()))

    def test_git_clone_is_shallow_quoted_and_never_prompts(self):
        sbx = FakeSandbox()
        self.run_with(sbx, source=("git", "https://github.com/org/my repo"))
        cmd, kw = sbx.commands.runs[0]
        self.assertEqual(cmd, "git clone --depth 1 'https://github.com/org/my repo' " + remote.REPO_DIR)
        self.assertEqual(kw["envs"], {"GIT_TERMINAL_PROMPT": "0"})

    def test_clone_failure_reports_git_error_and_skips_agent(self):
        sbx = FakeSandbox(clone_fails=True)
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertIn("fatal: repository not found", err)
        self.assertFalse(any(c.startswith("python agent.py") for c, _ in sbx.commands.runs))
        self.assertTrue(sbx.killed)

    def test_local_source_uploads_and_extracts_the_tarball(self):
        with tempfile.TemporaryDirectory() as src:
            Path(src, "README.md").write_text("hi\n")
            sbx = FakeSandbox()
            code, _, err = self.run_with(sbx, source=("local", src))
        self.assertEqual(code, 0)
        self.assertIn("repo/README.md", tar_members(sbx.files.store[remote.TARBALL]))
        self.assertIn(("tar -xzf %s -C %s" % (remote.TARBALL, remote.HOME)), [c for c, _ in sbx.commands.runs])
        self.assertIn("MB", err)  # upload size is shown before uploading

    def test_agent_failure_passes_exit_code_keeps_metrics_and_clears_stale_overview(self):
        self.out.mkdir(parents=True)
        (self.out / OVERVIEW_NAME).write_text("old run\n")
        sbx = FakeSandbox(agent_exit=1)
        code, _, _ = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertTrue((self.out / "metrics.json").exists())
        self.assertFalse((self.out / OVERVIEW_NAME).exists())
        self.assertTrue(sbx.killed)

    def test_timeout_is_reported_and_the_sandbox_killed(self):
        sbx = FakeSandbox(agent_times_out=True)
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertIn("error: sandbox timed out", err)
        self.assertTrue(sbx.killed)

    def test_keep_skips_kill_and_prints_the_id(self):
        self.args.keep = True
        sbx = FakeSandbox()
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 0)
        self.assertFalse(sbx.killed)
        self.assertIn("sbx-test", err)

    def test_kill_failure_does_not_change_the_result(self):
        sbx = FakeSandbox(kill_fails=True)
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 0)
        self.assertIn("warning: could not kill sandbox sbx-test", err)

    def test_missing_key_never_creates_a_sandbox(self):
        for env in ({"ANTHROPIC_API_KEY": "sk-test"}, {"E2B_API_KEY": "e2b_test"}):
            code, _, err = self.run_with(FakeSandbox(), env=env)
            self.assertEqual(code, 1)
            self.assertIn("not set", err)
        self.assertEqual(self.factory_calls, [])

    def test_sandbox_creation_failure_points_at_the_template_script(self):
        def failing_factory(**kwargs):
            raise SandboxException("404: template 'overview-agent' not found")
        err = io.StringIO()
        with mock.patch.dict(os.environ, KEYS, clear=True), redirect_stderr(err):
            code = remote.run_remote(("git", "https://h/x"), self.args, sandbox_factory=failing_factory)
        self.assertEqual(code, 1)
        self.assertIn("python e2b_template.py", err.getvalue())
        self.assertIn("404", err.getvalue())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest test_remote -v`
Expected: the 11 `RunRemoteTests` ERROR with `AttributeError: module 'remote' has no attribute 'run_remote'`.
The 10 tests from Task 2 still pass.

- [ ] **Step 3: Implement `run_remote`**

In `remote.py`, replace the import block with:

```python
import io
import os
import re
import shlex
import sys
import tarfile
from pathlib import Path
from typing import Tuple

from e2b import CommandExitException, Sandbox, SandboxException, TimeoutException

from agent import trace
from tools import OVERVIEW_NAME, is_ignored_dir, is_ignored_name
```

Append at the end of `remote.py`:

```python
def run_remote(source: Tuple[str, str], args, sandbox_factory=None) -> int:
    """One remote run. Returns the agent's exit code, or 1 when something around it fails."""
    missing = [k for k in ("E2B_API_KEY", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if missing:
        trace("error: %s not set (add it to .env)" % " and ".join(missing))
        return 1
    kind, where = source
    tarball = b""
    if kind == "local":
        tarball = make_tarball(where)
        trace("uploading %s (%.1f MB compressed)" % (where, len(tarball) / 1e6))

    factory = sandbox_factory or Sandbox.create
    try:
        sbx = factory(template=TEMPLATE, timeout=SANDBOX_TIMEOUT)
    except SandboxException as e:
        trace("error: could not start a sandbox from template '%s' (did you run python e2b_template.py?): %s"
              % (TEMPLATE, e))
        return 1
    try:
        return _run_in_sandbox(sbx, kind, where, tarball, output_dir(source, args.out), args)
    except TimeoutException:
        trace("error: sandbox timed out")
        return 1
    except SandboxException as e:
        trace("error: %s" % e)
        return 1
    finally:
        if args.keep:
            trace("sandbox kept for inspection: %s" % sbx.sandbox_id)
        else:
            try:
                sbx.kill()
            except Exception as e:  # a failed cleanup must not change the run's result
                trace("warning: could not kill sandbox %s: %s" % (sbx.sandbox_id, e))


def _run_in_sandbox(sbx, kind: str, where: str, tarball: bytes, out: Path, args) -> int:
    for rel in AGENT_FILES:
        sbx.files.write("%s/%s" % (AGENT_DIR, rel), (HERE / rel).read_text(encoding="utf-8"))
    if kind == "git":
        try:
            sbx.commands.run("git clone --depth 1 %s %s" % (shlex.quote(where), REPO_DIR),
                             envs={"GIT_TERMINAL_PROMPT": "0"}, timeout=CLONE_TIMEOUT)
        except CommandExitException as e:
            trace("error: git clone failed:\n%s" % e.stderr.strip())
            return 1
    else:
        sbx.files.write(TARBALL, tarball)
        sbx.commands.run("tar -xzf %s -C %s" % (TARBALL, HOME))

    cmd = "python agent.py %s --model %s --max-turns %d --max-tokens %d --metrics-json %s" % (
        REPO_DIR, shlex.quote(args.model), args.max_turns, args.max_tokens, METRICS)
    try:
        result = sbx.commands.run(cmd, cwd=AGENT_DIR, envs={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]},
                                  on_stderr=_echo, timeout=AGENT_TIMEOUT)
        code, summary = 0, result.stdout
    except CommandExitException as e:
        code, summary = e.exit_code, e.stdout

    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for remote_path, local_name in ((REPO_DIR + "/" + OVERVIEW_NAME, OVERVIEW_NAME), (METRICS, "metrics.json")):
        local = out / local_name
        if local.exists():
            local.unlink()  # never leave a previous run's file looking like this run's result
        if sbx.files.exists(remote_path):
            local.write_bytes(bytes(sbx.files.read(remote_path, format="bytes")))
            saved.append(local)
    print(summary.rstrip())
    for path in saved:
        print("Saved %s" % path)
    return code


def _echo(chunk: str) -> None:
    """Stream the agent's trace as it arrives (E2B passes raw chunks, newlines included)."""
    print(chunk, end="", file=sys.stderr, flush=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest -v`
Expected: all tests pass (test_tools, test_agent, and the 21 in test_remote).

- [ ] **Step 5: Commit**

```bash
git add remote.py test_remote.py
git commit -m "remote.py: run the agent in an E2B sandbox and download its results"
```

---

### Task 4: CLI entry point, template builder, config and docs

**Files:**
- Modify: `remote.py` (`main`)
- Create: `e2b_template.py`
- Modify: `test_remote.py` (`MainTests`, `TemplateTests`)
- Modify: `.env.example`, `.gitignore`, `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: `parse_source`, `RemoteError`, `run_remote`, `TEMPLATE` (Tasks 2-3); `agent.load_dotenv`,
  `agent.DEFAULT_MODEL`; `e2b.Template` (`from_python_image`, `apt_install`, `pip_install`, `to_dockerfile`,
  `build(template, alias, cpu_count=, memory_mb=, on_build_logs=)`), `e2b.default_build_logger`.
- Produces: `remote.main(argv=None) -> int`; `e2b_template.template()` (an `e2b` TemplateBuilder);
  `e2b_template.main()`.

- [ ] **Step 1: Write the failing tests**

Add above `if __name__ == "__main__":` in `test_remote.py`:

```python
class MainTests(unittest.TestCase):
    def test_bad_source_is_one_error_line_and_no_sandbox(self):
        err = io.StringIO()
        with mock.patch.object(remote, "load_dotenv"), mock.patch.object(remote, "run_remote") as run, \
                redirect_stderr(err):
            code = remote.main(["/definitely/not/here"])
        self.assertEqual(code, 1)
        self.assertIn("error: not a git URL or an existing directory", err.getvalue())
        run.assert_not_called()

    def test_flags_and_env_defaults_reach_run_remote(self):
        env = {"OVERVIEW_MODEL": "claude-opus-5-5", "OVERVIEW_MAX_TURNS": "7"}
        with mock.patch.object(remote, "load_dotenv"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(remote, "run_remote", return_value=0) as run:
            code = remote.main(["github.com/org/repo", "--keep"])
        self.assertEqual(code, 0)
        source, args = run.call_args[0]
        self.assertEqual(source, ("git", "https://github.com/org/repo"))
        self.assertEqual((args.model, args.max_turns, args.max_tokens, args.out, args.keep),
                         ("claude-opus-5-5", 7, 16000, "", True))


class TemplateTests(unittest.TestCase):
    def test_template_has_python_git_and_anthropic_and_no_secrets(self):
        import e2b_template
        from e2b import Template
        dockerfile = Template.to_dockerfile(e2b_template.template())
        self.assertIn("FROM python:3.12", dockerfile)
        self.assertIn("install -y git", dockerfile)
        self.assertIn("pip install anthropic", dockerfile)
        self.assertNotIn("API_KEY", dockerfile)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest test_remote.MainTests test_remote.TemplateTests -v`
Expected: ERROR, `AttributeError: module 'remote' has no attribute 'main'` and
`ModuleNotFoundError: No module named 'e2b_template'`.

- [ ] **Step 3: Add `main()` to `remote.py`**

Add `import argparse` to the stdlib imports, and change `from agent import trace` to
`from agent import DEFAULT_MODEL, load_dotenv, trace`. Then append:

```python
def main(argv=None) -> int:
    load_dotenv()
    env = os.environ.get
    ap = argparse.ArgumentParser(
        description="Write PROJECT_OVERVIEW.md for a repository, running the agent in an E2B sandbox.")
    ap.add_argument("source", help="git URL (https://..., git@..., github.com/org/repo) or a local folder")
    ap.add_argument("--model", default=env("OVERVIEW_MODEL", DEFAULT_MODEL))
    ap.add_argument("--max-turns", type=int, default=int(env("OVERVIEW_MAX_TURNS", "25")))
    ap.add_argument("--max-tokens", type=int, default=int(env("OVERVIEW_MAX_TOKENS", "16000")))
    ap.add_argument("--out", default="", help="results folder (default: overviews/<repo-name>/ in this project)")
    ap.add_argument("--keep", action="store_true", help="leave the sandbox running at the end, to inspect it")
    args = ap.parse_args(argv)
    try:
        source = parse_source(args.source)
    except RemoteError as e:
        trace("error: %s" % e)
        return 1
    try:
        return run_remote(source, args)
    except KeyboardInterrupt:  # run_remote's `finally` has already killed the sandbox
        trace("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create `e2b_template.py`**

```python
#!/usr/bin/env python3
"""Build the E2B sandbox template that remote.py runs the agent in.

Run once, and again only when the agent's dependencies change:  python e2b_template.py
The template is only the environment (Python, git, the anthropic SDK). The agent code is uploaded on every run,
and no API key is ever baked in.
"""
from __future__ import annotations

from e2b import Template, default_build_logger

from agent import load_dotenv
from remote import TEMPLATE


def template():
    return Template().from_python_image("3.12").apt_install("git").pip_install("anthropic")


def main() -> None:
    load_dotenv()  # for E2B_API_KEY
    Template.build(template(), TEMPLATE, cpu_count=1, memory_mb=1024, on_build_logs=default_build_logger())
    print("Built template %s" % TEMPLATE)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest -v`
Expected: all tests pass, 24 of them in test_remote.

- [ ] **Step 6: Config files**

`.env.example`: add these lines after `ANTHROPIC_API_KEY=`:

```
# Only for remote runs (python remote.py ...): from https://e2b.dev
E2B_API_KEY=
```

`.gitignore`: add a line `overviews/`.

- [ ] **Step 7: Docs**

`README.md`: append this section:

~~~markdown
## Remote run (E2B)
Runs the same agent inside an [E2B](https://e2b.dev) sandbox. The target repo is cloned there (git URL) or uploaded
(local folder, minus ignored folders and secret files), and the results land in `overviews/<repo-name>/`. Your
local folder is never modified.

```bash
# once: put E2B_API_KEY in .env, then build the sandbox template
python e2b_template.py
python remote.py https://github.com/org/repo      # or: github.com/org/repo
python remote.py ../dayNight                       # local folder
python remote.py ../dayNight --keep                # leave the sandbox running to inspect it
```

Files that are never uploaded or read are listed in `IGNORED_DIRS` and `IGNORED_FILE_PATTERNS` in `tools.py`. If a
project keeps secrets under another name, add the pattern there before running on it.
~~~

`CLAUDE.md`, in "Where responsibilities live": after the `agent.py` bullet, add:

```markdown
- `remote.py`: E2B orchestration only (sandbox, clone or upload, run `agent.py`, download results). No agent logic.
- `e2b_template.py`: the sandbox image (Python, git, anthropic). No secrets; agent code is uploaded per run.
```

and change the `tools.py` bullet's opening to `` `tools.py`: rules enforced in code (ignore lists, shared with
`remote.py`'s upload filter, ...`` (keep the rest of that bullet as is).

`CLAUDE.md`, in "Commands": replace the test line and add the remote commands so the block ends with:

```bash
python agent.py <repo_path>       # live run, needs an API key
python e2b_template.py            # once: build the E2B sandbox template (needs E2B_API_KEY)
python remote.py <git-url|path>   # live run in E2B; results in overviews/<repo-name>/
python3 -m unittest -v            # offline: test_tools.py (sandbox), test_agent.py (loop), test_remote.py (E2B, fake sandbox)
```

- [ ] **Step 8: Verify and commit**

Run: `.venv/bin/python -m unittest -v && .venv/bin/python remote.py --help`
Expected: all tests pass. The help text lists `source`, `--model`, `--max-turns`, `--max-tokens`, `--out` and
`--keep`.

```bash
git add remote.py e2b_template.py test_remote.py .env.example .gitignore README.md CLAUDE.md
git commit -m "remote.py CLI, E2B template builder, docs"
```

---

### Task 5: Live verification (needs the human: E2B key, and it spends money)

**Files:**
- Modify: `specs/e2b-remote-run.md` (Status line and a new `## Results (<date>)` section)

**Interfaces:**
- Consumes: everything above, plus a real `.env` with `ANTHROPIC_API_KEY` and `E2B_API_KEY`.
- Produces: the measured results recorded in the spec.

**Ask the human before each step that calls E2B or Anthropic.** These steps cost money and need their accounts.

- [ ] **Step 1: The human adds `E2B_API_KEY` to `.env`** (from e2b.dev, Hobby tier is enough). Check with
  `grep -c '^E2B_API_KEY=.\+' .env` → `1`. Never print the key.

- [ ] **Step 2: Build the template**

Run: `.venv/bin/python e2b_template.py`
Expected: build logs, then `Built template overview-agent`.

- [ ] **Step 3: Remote run on a local folder**

No copy needed: a remote run never writes into the source folder, and the tarball already skips `.git/` and any
existing `PROJECT_OVERVIEW.md`.
Before: `ls -la ../dayNight > /tmp/before.txt`
Run: `time .venv/bin/python remote.py ../dayNight`
Expected: the live trace on stderr, then `Saved …/overviews/dayNight/PROJECT_OVERVIEW.md` and
`…/metrics.json`, exit 0. `ls -la ../dayNight | diff /tmp/before.txt -` prints nothing (the folder is untouched).

- [ ] **Step 4: Remote run on a small public GitHub repo** (the human picks it)

Run: `time .venv/bin/python remote.py github.com/<org>/<repo>`
Expected: same output shape, results in `overviews/<repo>/`.

- [ ] **Step 5: Local comparison runs**

A local run writes `PROJECT_OVERVIEW.md` into the folder it analyzes, so use two fresh copies without `.git` or
an old overview:

```bash
rsync -a --exclude .git --exclude PROJECT_OVERVIEW.md ../dayNight/ /tmp/dayNight-local/
rsync -a --exclude .git --exclude PROJECT_OVERVIEW.md ../dayNight/ /tmp/dayNight-base/
```

Then:

```bash
# this branch
.venv/bin/python agent.py /tmp/dayNight-local --metrics-json /tmp/local-metrics.json

# baseline 622c0fd, in a throwaway worktree (.env is git-ignored, so copy it in)
git worktree add /tmp/overview-base 622c0fd
cp .env /tmp/overview-base/.env
.venv/bin/python /tmp/overview-base/agent.py /tmp/dayNight-base --metrics-json /tmp/base-metrics.json
git worktree remove --force /tmp/overview-base
```

- [ ] **Step 6: Record the results in the spec**

Set the Status line to `Status: implemented and measured · Baseline: 622c0fd`. Add `## Results (<date>)` with
a table of turns, tool calls, tool errors, `first_write_ok`, input, output and cache tokens, the agent's
`wall_seconds`, and the end-to-end `time` of each `remote.py` run, for: remote-local-folder, remote-git,
local-this-branch, local-baseline. Add one sentence on overview quality (side by side) and whether the Tier 3
files differ between baseline and this branch.

- [ ] **Step 7: Commit**

```bash
git add specs/e2b-remote-run.md
git commit -m "Spec: E2B remote run results"
```
