"""Offline tests for remote.py (fake sandbox, no E2B or Anthropic key needed)."""
from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

import remote


def tar_members(data: bytes):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


class SourceTests(unittest.TestCase):
    def test_urls_are_git(self):
        for url in ("https://github.com/org/repo", "http://host/x.git", "git@github.com:org/repo.git",
                    "ssh://git@host/r"):
            self.assertEqual(remote.parse_source(url), ("git", url))

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
            self.assertEqual(remote.output_dir(source), remote.HERE / "overviews" / name, source)

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


if __name__ == "__main__":
    unittest.main()
