"""Run the Product Overview Agent remotely, inside an E2B sandbox (`python main.py remote`).

The sandbox runs the same agent code as a local run; this module only moves things around. It uploads the
agent code and the target repo, runs the agent with the API key passed to that one command, and downloads
PROJECT_OVERVIEW.md and the run metrics into overviews/<repo-name>/. The local folder is never modified.
Build the sandbox template once first: python main.py build-template
"""
from __future__ import annotations

import io
import os
import re
import shlex
import sys
import tarfile
from pathlib import Path
from typing import List, Tuple

from e2b import AuthenticationException, CommandExitException, Sandbox, TimeoutException

from overview_agent.config import PROJECT_ROOT, trace
from overview_agent.tools import OVERVIEW_NAME, is_ignored_dir, is_ignored_name

AGENT_PACKAGE = "overview_agent"
AGENT_SUFFIXES = (".py", ".md")  # code and prompts; never caches or OS files like .DS_Store
TEMPLATE = "overview-agent"
SANDBOX_TIMEOUT = 15 * 60  # seconds, the whole sandbox
AGENT_TIMEOUT = 14 * 60    # seconds; E2B's per-command default (60s) is far shorter than an agent run
CLONE_TIMEOUT = 5 * 60
HOME = "/home/user"
AGENT_DIR = HOME + "/agent"
REPO_DIR = HOME + "/repo"
TARBALL = HOME + "/repo.tar.gz"
METRICS = HOME + "/metrics.json"

URL_PREFIXES = ("https://", "http://")
HOST_PATH = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/[^/]")  # github.com/org/repo, without a scheme
SSH_URL = re.compile(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$")       # git@github.com:org/repo.git
RESULT_FILES = (OVERVIEW_NAME, "metrics.json")


class RemoteError(Exception):
    """A problem with the command-line input, reported as one `error:` line."""


def parse_source(arg: str) -> Tuple[str, str]:
    """('git', url) or ('local', absolute path). An existing directory wins over a URL-looking name."""
    ssh = SSH_URL.match(arg)
    if ssh:  # the sandbox has no SSH key, so clone over https instead (public repos)
        return "git", "https://%s/%s" % ssh.groups()
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
    return PROJECT_ROOT / "overviews" / repo_name(source)


def agent_files(root: Path = PROJECT_ROOT) -> List[str]:
    """What the sandbox needs to run the agent: main.py plus the package's code and prompts, relative to `root`."""
    package = sorted(p for p in (root / AGENT_PACKAGE).rglob("*") if p.is_file() and p.suffix in AGENT_SUFFIXES)
    return ["main.py"] + [p.relative_to(root).as_posix() for p in package]


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


def run_remote(source: Tuple[str, str], args, sandbox_factory=None) -> int:
    """One remote run. Returns the agent's exit code, or 1 when something around it fails."""
    missing = [k for k in ("E2B_API_KEY", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if missing:
        trace("error: %s not set (add it to .env)" % " and ".join(missing))
        return 1
    # Clear the previous results before spending anything, so a failed run on any path can't leave an old
    # overview looking like this run's, and an unusable --out fails now instead of after the agent ran.
    out = output_dir(source, args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name in RESULT_FILES:
            (out / name).unlink(missing_ok=True)
    except OSError as e:
        trace("error: cannot use results folder %s: %s" % (out, e))
        return 1
    kind, where = source
    tarball = b""
    if kind == "local":
        tarball = make_tarball(where)
        trace("uploading %s (%.1f MB compressed)" % (where, len(tarball) / 1e6))

    factory = sandbox_factory or Sandbox.create
    try:
        sbx = factory(template=TEMPLATE, timeout=SANDBOX_TIMEOUT)
    except AuthenticationException as e:
        trace("error: E2B rejected the API key (check E2B_API_KEY): %s" % e)
        return 1
    except Exception as e:
        trace("error: could not start a sandbox from template '%s' (did you run python main.py build-template?): %s"
              % (TEMPLATE, e))
        return 1
    try:
        return _run_in_sandbox(sbx, kind, where, tarball, out, args)
    except TimeoutException:
        trace("error: sandbox timed out")
        return 1
    except Exception as e:  # SandboxException, network errors: one line, not a traceback (Ctrl-C still propagates)
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
    for rel in agent_files():
        sbx.files.write("%s/%s" % (AGENT_DIR, rel), (PROJECT_ROOT / rel).read_text(encoding="utf-8"))
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

    cmd = "python main.py local %s --model %s --max-turns %d --max-tokens %d --metrics-json %s" % (
        REPO_DIR, shlex.quote(args.model), args.max_turns, args.max_tokens, METRICS)
    try:
        result = sbx.commands.run(cmd, cwd=AGENT_DIR, envs={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]},
                                  on_stderr=_echo, timeout=AGENT_TIMEOUT)
        code, summary = 0, result.stdout
    except CommandExitException as e:
        code, summary = e.exit_code, e.stdout

    downloads = [(METRICS, "metrics.json")]
    if code == 0:  # only a successful run wrote a fresh overview; a cloned repo may carry an old committed one
        downloads.insert(0, (REPO_DIR + "/" + OVERVIEW_NAME, OVERVIEW_NAME))
    saved = []
    for remote_path, local_name in downloads:
        local = out / local_name
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
