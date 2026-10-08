"""Offline tests for remote.py (fake sandbox, no E2B or Anthropic key needed)."""
from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from e2b import AuthenticationException, CommandExitException, SandboxException, TimeoutException

from overview_agent import config, remote
from overview_agent.overview_format import OVERVIEW_NAME


def tar_members(data: bytes):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


class SourceTests(unittest.TestCase):
    def test_urls_are_git(self):
        for url in ("https://github.com/org/repo", "http://host/x.git"):
            self.assertEqual(remote.parse_source(url), ("git", url))

    def test_ssh_urls_become_https_because_the_sandbox_has_no_ssh_key(self):
        self.assertEqual(remote.parse_source("git@github.com:org/repo.git"), ("git", "https://github.com/org/repo.git"))
        self.assertEqual(remote.parse_source("ssh://git@github.com/org/repo"), ("git", "https://github.com/org/repo"))

    def test_scheme_less_host_path_becomes_https(self):
        self.assertEqual(remote.parse_source("github.com/org/repo"), ("git", "https://github.com/org/repo"))

    def test_existing_directory_is_local_even_if_named_like_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "thing.git"
            d.mkdir()
            self.assertEqual(remote.parse_source(str(d)), ("local", str(d.resolve())))

    def test_dot_resolves_to_real_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                source = remote.parse_source(".")
            finally:
                os.chdir(cwd)
            self.assertEqual(source, ("local", str(Path(tmp).resolve())))
            self.assertEqual(remote.repo_name(source), Path(tmp).resolve().name)

    def test_not_a_url_nor_a_directory_is_an_error(self):
        for bad in ("/definitely/not/here", "../nope", "foo.git"):
            with self.assertRaises(remote.RemoteError, msg=bad):
                remote.parse_source(bad)


class OutputDirTests(unittest.TestCase):
    def test_default_is_overviews_slash_repo_name(self):
        cases = {
            ("git", "https://github.com/org/repo.git"): "repo",
            ("git", "https://github.com/org/repo/"): "repo",
            ("git", "git@github.com:org/my-app.git"): "my-app",
            ("local", "/home/me/dayNight"): "dayNight",
        }
        for source, name in cases.items():
            self.assertEqual(remote.output_dir(source), config.PROJECT_ROOT / "overviews" / name, source)

    def test_explicit_out_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(remote.output_dir(("git", "https://h/x"), tmp), Path(tmp).resolve())


class TarballTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        (base / "outside.txt").write_text("secret")
        self.root = base / "build"  # the folder's own name is an ignored dir; its content must still upload
        for rel in ("README.md", "src/app.py", "src/secrets/vault.py", ".github/workflows/ci.yml",
                    ".env", "certs/id.pem", "config/credentials.json", "infra/prod.tfstate",
                    "node_modules/dep/index.js", ".git/HEAD", "ios/Pods/Lib/lib.swift", "yarn.lock"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        os.symlink(str(base / "outside.txt"), str(self.root / "link.txt"))
        self.data = remote.make_tarball(str(self.root))
        self.members = tar_members(self.data)

    def test_keeps_product_files_under_repo(self):
        for rel in ("README.md", "src/app.py", "src/secrets/vault.py", ".github/workflows/ci.yml"):
            self.assertIn("repo/" + rel, self.members)

    def test_drops_what_the_agent_may_not_read(self):
        names = "\n".join(self.members)
        for hidden in (".env", "id.pem", "credentials.json", "prod.tfstate", "node_modules", "Pods", "yarn.lock"):
            self.assertNotIn(hidden, names, hidden)
        self.assertNotIn("repo/.git", self.members)

    def test_symlink_is_stored_as_link_not_followed(self):
        self.assertTrue(self.members["repo/link.txt"].issym())
        with tarfile.open(fileobj=io.BytesIO(self.data), mode="r:gz") as tar:
            contents = [tar.extractfile(m).read() for m in tar.getmembers() if m.isreg()]
        self.assertNotIn(b"secret", contents)


class FakeFiles:
    def __init__(self):
        self.store = {}

    def write(self, path, data):
        self.store[path] = data

    def exists(self, path):
        return path in self.store

    def read(self, path, format="text"):
        data = self.store[path]
        return data.encode() if format == "bytes" and isinstance(data, str) else data


class FakeCommands:
    def __init__(self, sbx, agent_exit=0, clone_fails=False, agent_times_out=False, repo_has_overview=False):
        self.sbx, self.agent_exit, self.clone_fails, self.agent_times_out = sbx, agent_exit, clone_fails, agent_times_out
        self.repo_has_overview = repo_has_overview
        self.runs = []

    def run(self, cmd, **kw):
        self.runs.append((cmd, kw))
        if cmd.startswith("git clone") and self.clone_fails:
            raise CommandExitException(stderr="fatal: repository not found", stdout="", exit_code=128, error=None)
        if cmd.startswith("git clone") and self.repo_has_overview:  # an overview committed in the cloned repo
            self.sbx.files.store[remote.REPO_DIR + "/" + OVERVIEW_NAME] = "# Old: Product Overview\n"
        if cmd.startswith("python main.py local"):
            if self.agent_times_out:
                raise TimeoutException("command timed out")
            kw["on_stderr"]("[turn 1] reading README\n")
            self.sbx.files.store[remote.METRICS] = '{"turns": 1}\n'
            if self.agent_exit:
                raise CommandExitException(stderr="", stdout="no overview", exit_code=self.agent_exit, error=None)
            self.sbx.files.store[remote.REPO_DIR + "/" + OVERVIEW_NAME] = "# X: Product Overview\n"
            return SimpleNamespace(stdout="agent summary\n", stderr="", exit_code=0)
        return SimpleNamespace(stdout="", stderr="", exit_code=0)


class FakeSandbox:
    def __init__(self, kill_fails=False, **behavior):
        self.sandbox_id = "sbx-test"
        self.files = FakeFiles()
        self.commands = FakeCommands(self, **behavior)
        self.kill_fails = kill_fails
        self.killed = False

    def kill(self):
        self.killed = True
        if self.kill_fails:
            raise SandboxException("network blip")

    def agent_run(self):
        return next((cmd, kw) for cmd, kw in self.commands.runs if cmd.startswith("python main.py local"))


KEYS = {"E2B_API_KEY": "e2b_test", "ANTHROPIC_API_KEY": "sk-test"}


class RunRemoteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "out"
        self.args = SimpleNamespace(model="claude-sonnet-5-5", max_turns=25, max_tokens=16000,
                                    out=str(self.out), keep=False, engine="api")
        self.factory_calls = []

    def run_with(self, sbx, source=("git", "https://github.com/org/repo"), env=KEYS):
        def factory(**kwargs):
            self.factory_calls.append(kwargs)
            return sbx
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = remote.run_remote(source, self.args, sandbox_factory=factory)
        return code, out.getvalue(), err.getvalue()

    def test_success_downloads_results_and_kills_the_sandbox(self):
        sbx = FakeSandbox()
        code, out, err = self.run_with(sbx)
        self.assertEqual(code, 0)
        self.assertEqual(self.factory_calls, [{"template": remote.TEMPLATE, "timeout": remote.SANDBOX_TIMEOUT}])
        for rel in remote.agent_files():
            self.assertIn(remote.AGENT_DIR + "/" + rel, sbx.files.store)
        self.assertEqual((self.out / OVERVIEW_NAME).read_text(), "# X: Product Overview\n")
        self.assertEqual((self.out / "metrics.json").read_text(), '{"turns": 1}\n')
        self.assertIn("agent summary", out)
        self.assertIn("[turn 1] reading README", err)  # trace streamed live
        self.assertTrue(sbx.killed)

    def test_api_key_goes_only_to_the_agent_command(self):
        sbx = FakeSandbox()
        self.run_with(sbx)
        cmd, kw = sbx.agent_run()
        self.assertTrue(cmd.startswith("python main.py local %s --engine api --model " % remote.REPO_DIR), cmd)
        self.assertEqual(kw["envs"], {"ANTHROPIC_API_KEY": "sk-test"})
        self.assertEqual(kw["cwd"], remote.AGENT_DIR)
        self.assertEqual(kw["timeout"], remote.AGENT_TIMEOUT)
        self.assertIn("--model claude-sonnet-5-5 --max-turns 25 --max-tokens 16000", cmd)
        self.assertIn("--metrics-json " + remote.METRICS, cmd)
        others = [kw for c, kw in sbx.commands.runs if not c.startswith("python main.py local")]
        self.assertTrue(all("ANTHROPIC_API_KEY" not in (kw.get("envs") or {}) for kw in others))
        self.assertTrue(all("sk-test" not in str(v) for v in sbx.files.store.values()))

    def test_the_engine_is_forwarded_to_the_agent_command(self):
        self.args.engine = "agent-sdk"
        sbx = FakeSandbox()
        self.run_with(sbx)
        cmd, _ = sbx.agent_run()
        self.assertIn("--engine agent-sdk", cmd)

    def test_git_clone_is_shallow_quoted_and_never_prompts(self):
        sbx = FakeSandbox()
        self.run_with(sbx, source=("git", "https://github.com/org/my repo"))
        cmd, kw = sbx.commands.runs[0]
        self.assertEqual(cmd, "git clone --depth 1 'https://github.com/org/my repo' " + remote.REPO_DIR)
        self.assertEqual(kw["envs"], {"GIT_TERMINAL_PROMPT": "0"})

    def test_clone_failure_reports_git_error_and_skips_agent(self):
        sbx = FakeSandbox(clone_fails=True)
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertIn("fatal: repository not found", err)
        self.assertFalse(any(c.startswith("python main.py local") for c, _ in sbx.commands.runs))
        self.assertTrue(sbx.killed)

    def test_local_source_uploads_and_extracts_the_tarball(self):
        with tempfile.TemporaryDirectory() as src:
            Path(src, "README.md").write_text("hi\n")
            sbx = FakeSandbox()
            code, _, err = self.run_with(sbx, source=("local", src))
        self.assertEqual(code, 0)
        self.assertIn("repo/README.md", tar_members(sbx.files.store[remote.TARBALL]))
        self.assertIn(("tar -xzf %s -C %s" % (remote.TARBALL, remote.HOME)), [c for c, _ in sbx.commands.runs])
        self.assertIn("MB", err)  # upload size is shown before uploading

    def test_agent_failure_passes_exit_code_keeps_metrics_and_clears_stale_overview(self):
        self.out.mkdir(parents=True)
        (self.out / OVERVIEW_NAME).write_text("old run\n")
        sbx = FakeSandbox(agent_exit=1)
        code, _, _ = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertTrue((self.out / "metrics.json").exists())
        self.assertFalse((self.out / OVERVIEW_NAME).exists())
        self.assertTrue(sbx.killed)

    def test_timeout_is_reported_and_the_sandbox_killed(self):
        sbx = FakeSandbox(agent_times_out=True)
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertIn("error: sandbox timed out", err)
        self.assertTrue(sbx.killed)

    def test_keep_skips_kill_and_prints_the_id(self):
        self.args.keep = True
        sbx = FakeSandbox()
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 0)
        self.assertFalse(sbx.killed)
        self.assertIn("sbx-test", err)

    def test_kill_failure_does_not_change_the_result(self):
        sbx = FakeSandbox(kill_fails=True)
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 0)
        self.assertIn("warning: could not kill sandbox sbx-test", err)

    def test_missing_key_never_creates_a_sandbox(self):
        for env in ({"ANTHROPIC_API_KEY": "sk-test"}, {"E2B_API_KEY": "e2b_test"}):
            code, _, err = self.run_with(FakeSandbox(), env=env)
            self.assertEqual(code, 1)
            self.assertIn("not set", err)
        self.assertEqual(self.factory_calls, [])

    def test_sandbox_creation_failure_points_at_the_template_script(self):
        def failing_factory(**kwargs):
            raise SandboxException("404: template 'overview-agent' not found")
        err = io.StringIO()
        with mock.patch.dict(os.environ, KEYS, clear=True), redirect_stderr(err):
            code = remote.run_remote(("git", "https://h/x"), self.args, sandbox_factory=failing_factory)
        self.assertEqual(code, 1)
        self.assertIn("python main.py build-template", err.getvalue())
        self.assertIn("404", err.getvalue())

    def test_overview_committed_in_the_cloned_repo_is_not_presented_when_the_agent_fails(self):
        sbx = FakeSandbox(repo_has_overview=True, agent_exit=1)
        code, out, _ = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertFalse((self.out / OVERVIEW_NAME).exists())
        self.assertNotIn("Saved %s" % (self.out / OVERVIEW_NAME), out)

    def test_stale_results_are_cleared_even_when_the_agent_times_out(self):
        self.out.mkdir(parents=True)
        for name in (OVERVIEW_NAME, "metrics.json"):
            (self.out / name).write_text("old run\n")
        code, _, _ = self.run_with(FakeSandbox(agent_times_out=True))
        self.assertEqual(code, 1)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_unusable_out_folder_fails_before_creating_a_sandbox(self):
        self.out.write_text("I am a file, not a folder\n")
        code, _, err = self.run_with(FakeSandbox())
        self.assertEqual(code, 1)
        self.assertIn("error: cannot use results folder", err)
        self.assertEqual(self.factory_calls, [])

    def test_rejected_e2b_key_is_one_error_line(self):
        def factory(**kwargs):
            raise AuthenticationException("401: Unauthorized, please check your credentials.")
        err = io.StringIO()
        with mock.patch.dict(os.environ, KEYS, clear=True), redirect_stderr(err):
            code = remote.run_remote(("git", "https://h/x"), self.args, sandbox_factory=factory)
        self.assertEqual(code, 1)
        self.assertIn("error: E2B rejected the API key (check E2B_API_KEY)", err.getvalue())

    def test_unexpected_error_inside_the_sandbox_is_one_error_line_and_kills_it(self):
        sbx = FakeSandbox()
        sbx.files.write = mock.Mock(side_effect=ConnectionError("connection reset"))
        code, _, err = self.run_with(sbx)
        self.assertEqual(code, 1)
        self.assertIn("error: connection reset", err)
        self.assertTrue(sbx.killed)


class AgentFilesTests(unittest.TestCase):
    def test_real_package_is_uploaded_with_its_prompt(self):
        files = remote.agent_files()
        self.assertEqual(files[0], "main.py")
        for rel in ("overview_agent/agent.py", "overview_agent/config.py", "overview_agent/sandbox.py",
                    "overview_agent/prompts/overview_agent.md"):
            self.assertIn(rel, files)
        self.assertFalse(any("__pycache__" in rel for rel in files))

    def test_only_code_and_prompts_are_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("overview_agent/a.py", "overview_agent/prompts/p.md", "overview_agent/.DS_Store",
                        "overview_agent/__pycache__/a.cpython-39.pyc"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(b"\x00\xff")
            self.assertEqual(remote.agent_files(root),
                             ["main.py", "overview_agent/a.py", "overview_agent/prompts/p.md"])


class TemplateTests(unittest.TestCase):
    def test_template_has_python_git_and_anthropic_and_no_secrets(self):
        from overview_agent import e2b_template
        from e2b import Template
        dockerfile = Template.to_dockerfile(e2b_template.template())
        self.assertIn("FROM python:3.12", dockerfile)
        self.assertIn("install -y git", dockerfile)
        self.assertIn("pip install anthropic", dockerfile)
        self.assertNotIn("API_KEY", dockerfile)


if __name__ == "__main__":
    unittest.main()
