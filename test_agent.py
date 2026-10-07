"""Offline tests for the agent loop in agent.py (fake client, no API key needed)."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

import agent


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


if __name__ == "__main__":
    unittest.main()
