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
