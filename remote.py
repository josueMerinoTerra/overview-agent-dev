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
