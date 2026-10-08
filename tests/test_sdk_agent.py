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


def result(subtype="success", turns=2, text="done", denials=None, scale=1):
    """A ResultMessage. Like the real CLI, usage and cost are cumulative for the session: `scale` = queries so far."""
    usage = {k: v * scale for k, v in USAGE.items()}
    return ResultMessage(subtype=subtype, duration_ms=10, duration_api_ms=8, is_error=subtype != "success",
                         num_turns=turns, session_id="s1", total_cost_usd=0.01 * scale, usage=usage, result=text,
                         permission_denials=denials,
                         model_usage={"claude-sonnet-5-5": {"inputTokens": usage["input_tokens"], "costUSD": 0.01}})


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


async def says_done_again(client):  # the second query of the session: cumulative usage covers both
    return [result(scale=2)]


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
        sdk_only = {"sdk_cost_usd", "model_usage"}  # what only the SDK reports
        self.assertEqual(set(metrics) - sdk_only, set(json.loads(api_metrics.read_text())))

    def test_the_reminder_is_sent_once_when_nothing_was_written(self):
        code, client, metrics, err = self.run_engine(says_done, says_done_again)
        self.assertEqual(code, 1)
        self.assertEqual(client.prompts, [FIRST_MESSAGE, REMINDER])
        self.assertEqual(metrics["turns"], 4)  # num_turns is per query: summed

    def test_session_usage_and_cost_are_cumulative_so_the_last_result_wins(self):
        _, _, metrics, _ = self.run_engine(says_done, says_done_again)
        self.assertEqual((metrics["input_tokens"], metrics["cache_read_input_tokens"]), (10, 26))
        self.assertAlmostEqual(metrics["sdk_cost_usd"], 0.02)

    def test_per_model_usage_is_recorded_to_show_which_models_ran(self):
        _, _, metrics, _ = self.run_engine(writes_overview)
        self.assertEqual(list(metrics["model_usage"]), ["claude-sonnet-5-5"])

    def test_parent_claude_code_session_variables_do_not_reach_the_cli(self):
        seen = {}

        def factory(options):
            seen.update(os.environ)
            return FakeClient(options, [says_done, says_done_again], {})

        parent = {"CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_EFFORT": "max", "CLAUDECODE": "1", "KEEP_ME": "x"}
        with mock.patch.dict(os.environ, parent):
            self.run_engine(factory=factory)
            self.assertEqual(os.environ["CLAUDE_CODE_SESSION_ID"], "parent")  # restored afterwards
        self.assertEqual([k for k in seen if k.startswith("CLAUDE")], [])
        self.assertEqual(seen["KEEP_ME"], "x")

    def test_the_turn_limit_is_named_metrics_are_written_and_no_reminder_follows(self):
        code, client, metrics, err = self.run_engine(hits_max_turns)
        self.assertEqual(code, 1)
        self.assertEqual(client.prompts, [FIRST_MESSAGE])
        self.assertIn("--max-turns", err)
        self.assertEqual(metrics["turns"], 25)

    def test_calls_that_never_reached_our_code_and_denials_are_counted(self):
        code, _, metrics, _ = self.run_engine(one_call_reaches_us_one_does_not, says_done_again)
        self.assertEqual(metrics["schema_rejected_calls"], 1)
        self.assertEqual(metrics["denied_calls"], 1)
        # Every tool_use block counts as a call, as in the api engine, where such calls reach RepoSandbox and fail.
        self.assertEqual((metrics["tool_calls"], metrics["tool_errors"]), (3, 2))

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
