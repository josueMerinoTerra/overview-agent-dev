"""The api engine: a hand-written Messages API loop that runs each tool call in RepoSandbox and records metrics.

The instructions come from prompts/overview_agent.md (next to this file). The model explores the repo only
through the sandboxed tools, which enforce the hard rules. Run it with `python main.py local`.
"""
from __future__ import annotations

import sys
import time

import anthropic

from overview_agent.config import trace
from overview_agent.recorder import USAGE_KEYS, ToolRecorder, finish_run
from overview_agent.sandbox import RepoSandbox, ToolError
from overview_agent.task import FIRST_MESSAGE, REMINDER, load_instructions
from overview_agent.tool_schemas import TOOLS


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
    messages = [{"role": "user", "content": FIRST_MESSAGE}]
    final_text = ""
    nudged = False
    started = time.monotonic()
    recorder = ToolRecorder(sandbox)
    usage = dict.fromkeys(USAGE_KEYS, 0)
    turns = 0

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

        turns = turn
        for key in USAGE_KEYS:
            usage[key] += getattr(resp.usage, key, 0) or 0
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
            messages.append({"role": "user", "content": REMINDER})
            continue

        if text:
            trace("\n[turn %d] %s" % (turn, text))
        results = []
        for block in (b for b in resp.content if b.type == "tool_use"):
            out, is_error = recorder.call(block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        trace("warning: stopped after %d turns" % max_turns)

    # The api engine has no client-side schema check and no permission layer, so these are always 0.
    extra = {"schema_rejected_calls": 0, "denied_calls": 0}
    return finish_run("api", model, sandbox, recorder, usage, turns, started, final_text, metrics_json, extra)
