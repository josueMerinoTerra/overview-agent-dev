"""Offline tests for judge.py (fake Messages client)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from overview_agent import judge


def scores(value=4):
    return {c: {"score": value, "reason": "ok"} for c in judge.CRITERIA}


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=10, output_tokens=20, cache_creation_input_tokens=30,
                                cache_read_input_tokens=40)
        content = [SimpleNamespace(type="thinking", thinking=""),
                   SimpleNamespace(type="text", text=json.dumps(self.payload))]
        return SimpleNamespace(content=content, stop_reason=self.stop_reason, usage=usage)


class JudgeTests(unittest.TestCase):
    def test_request_caches_the_digest_and_asks_for_structured_scores(self):
        fake = FakeMessages(scores())
        judge.judge("# Acme: Product Overview\nBooks appointments.", "DIGEST", SimpleNamespace(messages=fake))
        kw = fake.calls[0]
        self.assertEqual(kw["model"], judge.JUDGE_MODEL)
        self.assertIn("DIGEST", kw["system"][1]["text"])
        self.assertEqual(kw["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Books appointments.", kw["messages"][0]["content"])
        dumped = json.dumps(kw).lower()
        self.assertNotIn("agent-sdk", dumped)
        self.assertNotIn("engine", dumped)

    def test_scores_total_and_usage_come_back(self):
        out = judge.judge("x", "d", SimpleNamespace(messages=FakeMessages(scores(3))))
        self.assertEqual(out["total"], 12)
        self.assertEqual(out["scores"]["coverage"], {"score": 3, "reason": "ok"})
        self.assertEqual(out["usage"]["cache_read_input_tokens"], 40)

    def test_out_of_range_score_is_a_judge_error(self):
        with self.assertRaises(judge.JudgeError):
            judge.judge("x", "d", SimpleNamespace(messages=FakeMessages(scores(9))))

    def test_refusal_is_a_judge_error(self):
        with self.assertRaises(judge.JudgeError):
            judge.judge("x", "d", SimpleNamespace(messages=FakeMessages(scores(), stop_reason="refusal")))

    def test_digest_has_the_tree_and_tier_1_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("Acme books appointments.\n")
            (root / "package.json").write_text('{"name": "acme"}\n')
            (root / ".env").write_text("SECRET=hunter2\n")
            (root / "src").mkdir()
            (root / "src" / "app.ts").write_text("const internal = 1;\n")
            (root / "docs").mkdir()
            (root / "docs" / "index.md").write_text("Docs home.\n")
            digest = judge.build_digest(tmp)
        self.assertIn("## File tree", digest)
        self.assertIn("Acme books appointments.", digest)
        self.assertIn('"name": "acme"', digest)
        self.assertIn("Docs home.", digest)
        self.assertNotIn("hunter2", digest)
        self.assertNotIn("const internal", digest)


if __name__ == "__main__":
    unittest.main()
