#!/usr/bin/env python3
"""Product Overview Agent: writes PROJECT_OVERVIEW.md (what a product does, not how) for a repository.

Usage:
  python main.py local [repo_path] [--engine api|agent-sdk] [--model M] [--max-turns N] [--max-tokens N]
                       [--metrics-json PATH]
  python main.py remote <git-url | local-path> [--engine api|agent-sdk] [--model M] [--max-turns N]
                        [--max-tokens N] [--out DIR] [--keep]
  python main.py bench <local-path> [--model M] [--max-turns N] [--out DIR] [--max-usd X]
  python main.py build-template

`local` runs the agent on this machine and writes into the repo. `remote` runs the same agent in an E2B sandbox
and downloads the results into overviews/<repo-name>/. `build-template` builds that sandbox image (once).
`--engine` picks who runs the loop: our Messages API loop (api) or the Claude Agent SDK (agent-sdk). `bench` runs
both engines on one repo, locally and in E2B, judges the overviews and writes bench/<timestamp>/summary.md.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from overview_agent import agent
from overview_agent.config import DEFAULT_MODEL, ENGINES, load_dotenv, trace


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--model", default=env("OVERVIEW_MODEL", DEFAULT_MODEL))
    shared.add_argument("--max-turns", type=int, default=env("OVERVIEW_MAX_TURNS", "25"))
    shared.add_argument("--max-tokens", type=int, default=env("OVERVIEW_MAX_TOKENS", "16000"))
    engine = argparse.ArgumentParser(add_help=False)
    engine.add_argument("--engine", default=env("OVERVIEW_ENGINE", "api"), choices=ENGINES,
                        help="api: our Messages API loop; agent-sdk: the Claude Agent SDK "
                             "(default: $OVERVIEW_ENGINE or api)")

    parser = argparse.ArgumentParser(description="Write PROJECT_OVERVIEW.md for a repository.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    local = commands.add_parser(
        "local", parents=[shared, engine], help="run the agent on this machine",
        description="Write PROJECT_OVERVIEW.md for a local repository.")
    local.add_argument("repo", nargs="?", default=env("OVERVIEW_REPO_PATH", "."),
                       help="repository root (default: $OVERVIEW_REPO_PATH or the current directory)")
    local.add_argument("--metrics-json", default="", help="also write the run metrics as JSON to this path")
    local.set_defaults(handler=run_local)

    remote = commands.add_parser(
        "remote", parents=[shared, engine], help="run the agent in an E2B sandbox",
        description="Write PROJECT_OVERVIEW.md for a repository, running the agent in an E2B sandbox.")
    remote.add_argument("source", help="git URL (https://..., git@..., github.com/org/repo) or a local folder")
    remote.add_argument("--out", default="", help="results folder (default: overviews/<repo-name>/ in this project)")
    remote.add_argument("--keep", action="store_true", help="leave the sandbox running at the end, to inspect it")
    remote.set_defaults(handler=run_remote)

    bench = commands.add_parser(
        "bench", parents=[shared], help="benchmark both engines on one repo (8 runs, about $2)",
        description="Run both engines on one local repo (1 local + 3 E2B runs each), judge the overviews, "
                    "and write bench/<timestamp>/summary.md.")
    bench.add_argument("repo", help="local repository folder (never written: runs use a filtered copy)")
    bench.add_argument("--out", default="",
                       help="results folder; pass an earlier one to resume (default: bench/<timestamp>/)")
    bench.add_argument("--max-usd", type=float, default=3.0,
                       help="stop before the estimated spend passes this (default 3.00)")
    bench.set_defaults(handler=run_bench)

    template = commands.add_parser("build-template", help="build the E2B sandbox template (once)")
    template.set_defaults(handler=build_template)
    return parser


def run_local(args: argparse.Namespace) -> int:
    env = os.environ.get
    if not (env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN")):
        trace("note: ANTHROPIC_API_KEY is not set (add it to .env); relying on an `ant auth login` profile if one exists")
    if args.engine == "agent-sdk":
        try:  # import_module (not `from … import`) so tests can stand in a fake or a missing module via sys.modules
            sdk_agent = importlib.import_module("overview_agent.sdk_agent")  # needs claude-agent-sdk (Python 3.10+)
        except ImportError as e:
            trace("error: the agent-sdk engine needs the claude-agent-sdk package "
                  "(Python 3.10+; pip install -r requirements.txt): %s" % e)
            return 1
        return sdk_agent.run(args.repo, args.model, args.max_turns, args.metrics_json)
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


def run_bench(args: argparse.Namespace) -> int:
    from overview_agent import bench  # imports remote (e2b) lazily, like the remote command

    try:
        return bench.run_bench(args.repo, args)
    except KeyboardInterrupt:
        trace("interrupted; resume with --out pointing at the same folder")
        return 130


def build_template(args: argparse.Namespace) -> int:
    from overview_agent import e2b_template  # needs the e2b package

    e2b_template.build()
    return 0


def main(argv=None) -> int:
    load_dotenv()  # before parsing: the option defaults read the environment
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "engine", "api") not in ENGINES:  # argparse doesn't check `choices` against an env default
        parser.error("OVERVIEW_ENGINE must be one of: %s" % ", ".join(ENGINES))
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
