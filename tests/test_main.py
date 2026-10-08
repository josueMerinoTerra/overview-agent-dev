"""Offline tests for the command line in main.py (the agent and E2B are mocked)."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import main
from overview_agent import e2b_template, remote
from overview_agent.config import DEFAULT_MODEL, PROJECT_ROOT


class LocalCommandTests(unittest.TestCase):
    def test_flags_and_env_defaults_reach_agent_run(self):
        env = {"OVERVIEW_MODEL": "claude-opus-5-5", "OVERVIEW_MAX_TOKENS": "8000",
               "OVERVIEW_REPO_PATH": "/tmp/r", "ANTHROPIC_API_KEY": "sk-test"}
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(main.agent, "run", return_value=0) as run:
            code = main.main(["local", "--max-turns", "9", "--metrics-json", "m.json"])
        self.assertEqual(code, 0)
        run.assert_called_once_with("/tmp/r", "claude-opus-5-5", 9, 8000, "m.json")

    def test_missing_api_key_prints_a_note_and_still_runs(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(main.agent, "run", return_value=1) as run, redirect_stderr(err):
            code = main.main(["local", "some/repo"])
        self.assertEqual(code, 1)
        self.assertIn("note: ANTHROPIC_API_KEY is not set", err.getvalue())
        run.assert_called_once_with("some/repo", DEFAULT_MODEL, 25, 16000, "")

    def test_agent_sdk_engine_runs_sdk_agent_without_max_tokens(self):
        fake = mock.MagicMock()
        fake.run.return_value = 0
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.dict(sys.modules, {"overview_agent.sdk_agent": fake}), \
                mock.patch.object(main.agent, "run") as api_run:
            code = main.main(["local", "r", "--engine", "agent-sdk", "--metrics-json", "m.json"])
        self.assertEqual(code, 0)
        fake.run.assert_called_once_with("r", DEFAULT_MODEL, 25, "m.json")
        api_run.assert_not_called()

    def test_engine_defaults_to_overview_engine_from_the_environment(self):
        fake = mock.MagicMock()
        fake.run.return_value = 0
        with mock.patch.object(main, "load_dotenv"), \
                mock.patch.dict(os.environ, {"OVERVIEW_ENGINE": "agent-sdk", "ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.dict(sys.modules, {"overview_agent.sdk_agent": fake}):
            main.main(["local", "r"])
        fake.run.assert_called_once()

    def test_missing_agent_sdk_package_is_one_error_line(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.dict(sys.modules, {"overview_agent.sdk_agent": None}), redirect_stderr(err):
            code = main.main(["local", "r", "--engine", "agent-sdk"])
        self.assertEqual(code, 1)
        self.assertIn("error: the agent-sdk engine needs the claude-agent-sdk package", err.getvalue())


class RemoteCommandTests(unittest.TestCase):
    def test_bad_source_is_one_error_line_and_no_sandbox(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), mock.patch.object(remote, "run_remote") as run, \
                redirect_stderr(err):
            code = main.main(["remote", "/definitely/not/here"])
        self.assertEqual(code, 1)
        self.assertIn("error: not a git URL or an existing directory", err.getvalue())
        run.assert_not_called()

    def test_flags_and_env_defaults_reach_run_remote(self):
        env = {"OVERVIEW_MODEL": "claude-opus-5-5", "OVERVIEW_MAX_TURNS": "7", "OVERVIEW_ENGINE": "agent-sdk"}
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(remote, "run_remote", return_value=0) as run:
            code = main.main(["remote", "github.com/org/repo", "--keep"])
        self.assertEqual(code, 0)
        source, args = run.call_args[0]
        self.assertEqual(source, ("git", "https://github.com/org/repo"))
        self.assertEqual((args.model, args.max_turns, args.max_tokens, args.out, args.keep),
                         ("claude-opus-5-5", 7, 16000, "", True))
        self.assertEqual(args.engine, "agent-sdk")

    def test_ctrl_c_reports_interrupted_and_exits_130(self):
        err = io.StringIO()
        with mock.patch.object(main, "load_dotenv"), \
                mock.patch.object(remote, "run_remote", side_effect=KeyboardInterrupt), redirect_stderr(err):
            code = main.main(["remote", "github.com/org/repo"])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", err.getvalue())


class BuildTemplateCommandTests(unittest.TestCase):
    def test_build_template_builds_once(self):
        with mock.patch.object(main, "load_dotenv"), mock.patch.object(e2b_template, "build") as build:
            code = main.main(["build-template"])
        self.assertEqual(code, 0)
        build.assert_called_once_with()


class MalformedEnvTests(unittest.TestCase):
    def test_build_template_ignores_a_malformed_max_turns(self):
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"OVERVIEW_MAX_TURNS": "abc"}, clear=True), \
                mock.patch.object(e2b_template, "build") as build:
            code = main.main(["build-template"])
        self.assertEqual(code, 0)
        build.assert_called_once_with()

    def test_local_reports_a_malformed_max_turns_as_a_usage_error(self):
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"OVERVIEW_MAX_TURNS": "abc"}, clear=True), \
                mock.patch.object(main.agent, "run") as run, redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            main.main(["local", "r"])
        self.assertEqual(cm.exception.code, 2)
        run.assert_not_called()

    def test_an_unknown_overview_engine_is_a_usage_error(self):
        with mock.patch.object(main, "load_dotenv"), mock.patch.dict(os.environ, {"OVERVIEW_ENGINE": "gpt"}, clear=True), \
                mock.patch.object(main.agent, "run") as run, redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            main.main(["local", "r"])
        self.assertEqual(cm.exception.code, 2)
        run.assert_not_called()


class EntryPointTests(unittest.TestCase):
    def test_no_subcommand_is_a_usage_error(self):
        with mock.patch.object(main, "load_dotenv"), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            main.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_local_works_without_the_e2b_package(self):
        # The E2B sandbox image has no e2b package; `main.py local` must not need it.
        code = "import sys; sys.modules['e2b'] = None\nimport main\nmain.main(['local', '--help'])\n"
        result = subprocess.run([sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--metrics-json", result.stdout)

    def test_local_api_engine_works_without_the_agent_sdk_package(self):
        code = ("import sys; sys.modules['e2b'] = None; sys.modules['claude_agent_sdk'] = None\n"
                "import main\nmain.main(['local', '--help'])\n")
        result = subprocess.run([sys.executable, "-c", code], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--engine", result.stdout)

    def test_runs_from_any_working_directory(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            result = subprocess.run([sys.executable, str(PROJECT_ROOT / "main.py"), "local", "--help"],
                                    cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--metrics-json", result.stdout)


if __name__ == "__main__":
    unittest.main()
