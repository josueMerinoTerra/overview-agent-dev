#!/usr/bin/env python3
"""Product Overview Agent: writes PROJECT_OVERVIEW.md for a local repository.

Usage:  python agent.py [repo_path] [--model MODEL] [--max-turns N]

The instructions come from prompts/overview_agent.md (relative to this file). The model explores the
repo only through the sandboxed tools in tools.py, which enforce the hard rules.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic

from overview_agent.config import DEFAULT_MODEL, load_dotenv, trace
from overview_agent.tools import OVERVIEW_NAME, TIER3_MAX_FILES, TOOLS, RepoSandbox, ToolError


def load_instructions() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "overview_agent.md"
    if not path.is_file():
        sys.exit("error: prompts/overview_agent.md not found next to agent.py")
    return path.read_text(encoding="utf-8")


def run(root: str, model: str, max_turns: int, max_tokens: int = 16000, metrics_json: str = "") -> int:
    try:
        sandbox = RepoSandbox(root, log=trace)
    except ToolError as e:
        sys.exit("error: %s" % e)

    client = anthropic.Anthropic()
    system = [{
        "type": "text",
        "text": load_instructions(),
        "cache_control": {"type": "ephemeral"},
    }]
    messages = [{
        "role": "user",
        "content": (
            "Create %s for the repository at the sandbox root (paths are relative to it: '.'). "
            "Follow Stage 1, 2 and 3 of your instructions." % OVERVIEW_NAME
        ),
    }]
    final_text = ""
    nudged = False
    started = time.monotonic()
    usage_keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    metrics = dict.fromkeys(("turns", "tool_calls", "tool_errors", "write_attempts") + usage_keys, 0)
    metrics["first_write_ok"] = None

    for turn in range(1, max_turns + 1):
        try:
            # The system block caches tools + instructions; the top-level breakpoint follows the end of the
            # conversation, so each turn reads all earlier turns from cache instead of re-sending them.
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, system=system, tools=TOOLS, messages=messages,
                cache_control={"type": "ephemeral"},
            )
        except anthropic.AuthenticationError:
            sys.exit("error: authentication failed. Set ANTHROPIC_API_KEY (or run `ant auth login`).")
        except anthropic.APIStatusError as e:
            sys.exit("error: API returned %s: %s" % (e.status_code, e.message))
        except anthropic.APIConnectionError as e:
            sys.exit("error: could not reach the API: %s" % e)

        metrics["turns"] = turn
        for key in usage_keys:
            metrics[key] += getattr(resp.usage, key, 0) or 0
        messages.append({"role": "assistant", "content": resp.content})
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()

        if resp.stop_reason == "refusal":
            sys.exit("error: the model declined this request (stop_reason=refusal)")
        if resp.stop_reason == "max_tokens":
            sys.exit("error: response hit max_tokens before finishing")

        if resp.stop_reason != "tool_use":
            final_text = text
            if sandbox.overview_written or nudged:
                break
            nudged = True  # finished without writing the file: one reminder, then give up
            messages.append({"role": "user", "content": "You have not written %s yet. Call write_overview." % OVERVIEW_NAME})
            continue

        if text:
            trace("\n[turn %d] %s" % (turn, text))
        results = []
        for block in (b for b in resp.content if b.type == "tool_use"):
            trace("  -> %s %s" % (block.name, block.input))
            try:
                out, is_error = sandbox.call(block.name, block.input), False
            except ToolError as e:
                out, is_error = str(e), True
            except Exception as e:  # a tool bug must not kill the run; let the model see it
                out, is_error = "internal tool error: %s" % e, True
            metrics["tool_calls"] += 1
            if block.name == "write_overview":
                metrics["write_attempts"] += 1
                if metrics["first_write_ok"] is None:
                    metrics["first_write_ok"] = not is_error
            if is_error:
                metrics["tool_errors"] += 1
                trace("     ! %s" % out.splitlines()[0])
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        trace("warning: stopped after %d turns" % max_turns)

    # Ground truth from the sandbox, not from the model's self-report.
    print(final_text)
    print("\n---")
    print("Tier 3 files read: %d/%d" % (len(sandbox.tier3), TIER3_MAX_FILES))
    for rel, q in sandbox.tier3.items():
        print("  %s -> question %d" % (rel, q))
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
    if sandbox.overview_written:
        print("Wrote %s" % (sandbox.root / OVERVIEW_NAME))
        return 0
    print("error: %s was not written" % OVERVIEW_NAME, file=sys.stderr)
    return 1


def main() -> None:
    load_dotenv()
    env = os.environ.get
    ap = argparse.ArgumentParser(description="Write PROJECT_OVERVIEW.md for a local repository.")
    ap.add_argument("repo", nargs="?", default=env("OVERVIEW_REPO_PATH", "."),
                    help="repository root (default: $OVERVIEW_REPO_PATH or the current directory)")
    ap.add_argument("--model", default=env("OVERVIEW_MODEL", DEFAULT_MODEL))
    ap.add_argument("--max-turns", type=int, default=int(env("OVERVIEW_MAX_TURNS", "25")))
    ap.add_argument("--max-tokens", type=int, default=int(env("OVERVIEW_MAX_TOKENS", "16000")))
    ap.add_argument("--metrics-json", default="", help="also write the run metrics as JSON to this path")
    args = ap.parse_args()
    if not (env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN")):
        trace("note: ANTHROPIC_API_KEY is not set (add it to .env); relying on an `ant auth login` profile if one exists")
    sys.exit(run(args.repo, args.model, args.max_turns, args.max_tokens, args.metrics_json))


if __name__ == "__main__":
    main()
