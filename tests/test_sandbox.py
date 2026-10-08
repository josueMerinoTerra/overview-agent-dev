"""Offline tests for the sandbox rules in overview_agent/sandbox.py (no API key needed)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from overview_agent.ignore_rules import is_ignored_dir, is_ignored_name
from overview_agent.overview_format import OVERVIEW_NAME
from overview_agent.sandbox import RepoSandbox, ToolError

GOOD = """# Acme: Product Overview

## In one sentence
Acme lets bakery owners take pre-orders.

## Problem & core user
Bakery owners lose sales to missed phone orders.

## Key features
- Pre-orders — customers reserve items
- Pickup slots — owners cap capacity
- Reminders — customers get a text

## Main user workflow
1. Customer opens the shop page
2. Customer picks items and a pickup slot
3. Owner confirms the order

## Evidence & confidence
- Problem & core user: High (`README.md`)
- Key features: Medium (`src/orders/`)
- Main user workflow: Low (`README.md`)

## Open questions
- Is payment taken online?
"""


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        for rel, text in {
            "README.md": "# Acme\nBakery pre-orders\n",
            "package.json": '{"name": "acme"}',
            "package-lock.json": "{}",
            ".env": "SECRET=1",
            "node_modules/dep/index.js": "x",
            "src/orders/order.js": "\n".join("line %d" % i for i in range(300)),
            "src/orders/order.test.js": "describe('creates an order', () => {})\n",
            "src/a.js": "a", "src/b.js": "b", "src/c.js": "c", "src/d.js": "d", "src/e.js": "e", "src/f.js": "f",
        }.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (Path(self._tmp.name) / "outside.txt").write_text("secret")
        self.sb = RepoSandbox(str(self.root), log=lambda m: None)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_ignored_things_are_invisible(self):
        tree = self.sb.list_tree(".", 3)
        for hidden in ("node_modules", "package-lock.json", ".env"):
            self.assertNotIn(hidden, tree)
        self.assertIn("README.md", tree)
        self.assertNotIn("index.js", self.sb.find_files("*index*"))
        self.assertEqual(self.sb.grep("SECRET"), "(no matches)")

    def test_ignored_and_escaping_paths_refused(self):
        for bad in ("node_modules/dep/index.js", "package-lock.json", ".env", "../outside.txt"):
            with self.assertRaises(ToolError, msg=bad):
                self.sb.read_file(bad, 3, 1, "x")

    def test_symlink_escape_refused(self):
        os.symlink(str(Path(self._tmp.name) / "outside.txt"), str(self.root / "link.txt"))
        with self.assertRaises(ToolError):
            self.sb.read_file("link.txt", 3, 1, "x")

    def test_tier1_only_curated_files(self):
        self.assertIn("Bakery", self.sb.read_file("README.md", 1))
        self.assertIn("acme", self.sb.read_file("package.json", 1))
        with self.assertRaises(ToolError):
            self.sb.read_file("src/orders/order.js", 1)

    def test_tier1_files_do_not_spend_tier3_budget(self):
        self.sb.read_file("README.md", 3)
        self.assertEqual(len(self.sb.tier3), 0)

    def test_tier3_requires_question_and_reason(self):
        with self.assertRaises(ToolError):
            self.sb.read_file("src/a.js", 3)
        with self.assertRaises(ToolError):
            self.sb.read_file("src/a.js", 3, 4, "why")
        with self.assertRaises(ToolError):
            self.sb.read_file("src/a.js", 3, 1, "  ")

    def test_tier3_truncates_at_120_lines(self):
        out = self.sb.read_file("src/orders/order.js", 3, 2, "need the order model")
        self.assertEqual(len(out.splitlines()), 121)  # header + 120
        self.assertIn("truncated", out.splitlines()[0])

    def test_tier3_budget_is_five_distinct_files(self):
        for name in "abcde":
            self.sb.read_file("src/%s.js" % name, 3, 1, "gap")
        self.sb.read_file("src/a.js", 3, 1, "re-read of an already counted file is fine")
        with self.assertRaises(ToolError):
            self.sb.read_file("src/f.js", 3, 1, "sixth file")
        self.assertEqual(len(self.sb.tier3), 5)

    def test_grep_returns_fragments_only(self):
        out = self.sb.grep(r"describe\(['\"][^'\"]+", "*.test.*")
        self.assertIn("describe('creates an order", out)
        self.assertNotIn("=>", out)

    def test_write_overview_validates_and_writes_only_that_file(self):
        before = self.snapshot()
        for bad in ("# hi", GOOD.replace("## Open questions", "## Questions"), GOOD + "word " * 800):
            with self.assertRaises(ToolError):
                self.sb.write_overview(bad)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.sb.overview_written)

        msg = self.sb.write_overview(GOOD)
        self.assertIn("Wrote", msg)
        after = self.snapshot()
        new = set(after) - set(before)
        self.assertEqual({Path(p).name for p in new}, {OVERVIEW_NAME})
        self.assertTrue(all(after[p] == before[p] for p in before))

    def test_write_overview_rejects_too_many_bullets(self):
        many = GOOD.replace("- Reminders — customers get a text", "\n".join("- f%d — x" % i for i in range(6)))
        with self.assertRaises(ToolError):
            self.sb.write_overview(many)

    def test_write_overview_warns_on_phantom_evidence(self):
        msg = self.sb.write_overview(GOOD.replace("`README.md`", "`docs/nope.md`", 1))
        self.assertIn("not found", msg)

    def test_overview_itself_is_hidden_from_reads(self):
        self.sb.write_overview(GOOD)
        with self.assertRaises(ToolError):
            self.sb.read_file(OVERVIEW_NAME, 1)

    def test_dispatch_unknown_tool_and_missing_args(self):
        with self.assertRaises(ToolError):
            self.sb.call("rm", {})
        with self.assertRaises(ToolError):
            self.sb.call("find_files", {})


class ExpandedIgnoreTests(unittest.TestCase):
    """Secrets and library folders that are never product code, in any repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for rel in ("README.md", "config/credentials.json", "infra/prod.tfstate", "certs/push.p12",
                    "ios/Pods/Lib/lib.swift", "src/secrets/vault.py"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        self.sb = RepoSandbox(str(self.root), log=lambda m: None)

    def test_new_secrets_and_library_folders_are_refused(self):
        for bad in ("config/credentials.json", "infra/prod.tfstate", "certs/push.p12", "ios/Pods/Lib/lib.swift"):
            with self.assertRaises(ToolError, msg=bad):
                self.sb.read_file(bad, 3, 1, "x")

    def test_new_ignores_are_hidden_but_generic_names_stay_visible(self):
        tree = self.sb.list_tree(".", 3)
        self.assertNotIn("Pods", tree)
        self.assertNotIn("credentials.json", tree)
        self.assertIn("vault.py", tree)  # `secrets/` can be product code, so it is not built in

    def test_public_helpers(self):
        for name in ("node_modules", ".git", "Pods", ".terraform"):
            self.assertTrue(is_ignored_dir(name), name)
        for name in ("secrets", "src", "target", ".github"):
            self.assertFalse(is_ignored_dir(name), name)
        for name in (".env", ".env.local", "yarn.lock", "app.min.js", "id_rsa", "id_rsa.pub", "prod.tfvars",
                     "service-account-prod.json", "credentials.json", ".npmrc", ".envrc", ".git-credentials"):
            self.assertTrue(is_ignored_name(name), name)
        for name in ("README.md", "secrets-policy.md", "credentials.py", "package.json"):
            self.assertFalse(is_ignored_name(name), name)


if __name__ == "__main__":
    unittest.main()
