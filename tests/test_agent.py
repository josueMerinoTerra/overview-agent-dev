"""Offline tests for the agent loop in agent.py (fake client, no API key needed)."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from overview_agent import agent


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")],
                               stop_reason="end_turn", usage=usage)


class AgentLoopTests(unittest.TestCase):
    def test_every_request_caches_the_conversation(self):
        fake = SimpleNamespace(messages=FakeMessages())
        with tempfile.TemporaryDirectory() as repo, \
                mock.patch.object(agent.anthropic, "Anthropic", return_value=fake), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            agent.run(repo, "claude-sonnet-5-5", max_turns=3)
        calls = fake.messages.calls
        self.assertGreaterEqual(len(calls), 2)  # first answer + the "you have not written it" nudge
        for kwargs in calls:
            self.assertEqual(kwargs["cache_control"], {"type": "ephemeral"})
            self.assertEqual(kwargs["system"][0]["cache_control"], {"type": "ephemeral"})


    def test_metrics_name_the_engine_and_the_shared_counters(self):
        fake = SimpleNamespace(messages=FakeMessages())
        with tempfile.TemporaryDirectory() as repo, \
                mock.patch.object(agent.anthropic, "Anthropic", return_value=fake), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = os.path.join(repo, "m.json")
            agent.run(repo, "claude-sonnet-5-5", max_turns=3, metrics_json=path)
            with open(path) as fh:
                metrics = json.load(fh)
        self.assertEqual(metrics["engine"], "api")
        self.assertEqual(metrics["turns"], 2)
        for key in ("duplicate_calls", "rejected_calls", "schema_rejected_calls", "denied_calls"):
            self.assertEqual(metrics[key], 0)


if __name__ == "__main__":
    unittest.main()
