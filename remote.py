#!/usr/bin/env python3
"""Run the Product Overview Agent remotely, inside an E2B sandbox.

Usage:  python remote.py <git-url | local-path> [--model M] [--max-turns N] [--max-tokens N] [--out DIR] [--keep]

The sandbox runs the same agent.py/tools.py as a local run; this script only moves things around. It uploads the
agent code and the target repo, runs the agent with the API key passed to that one command, and downloads
PROJECT_OVERVIEW.md and the run metrics into overviews/<repo-name>/. The local folder is never modified.
Build the sandbox template once first: python e2b_template.py
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shlex
import sys
import tarfile
from pathlib import Path
from typing import Tuple

from e2b import CommandExitException, Sandbox, SandboxException, TimeoutException

from agent import DEFAULT_MODEL, load_dotenv, trace
from tools import OVERVIEW_NAME, is_ignored_dir, is_ignored_name

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
