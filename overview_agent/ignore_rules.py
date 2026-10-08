"""What the agent never reads and remote.py never uploads: library folders, lockfiles, generated code, secrets."""
from __future__ import annotations

import fnmatch

from overview_agent.overview_format import OVERVIEW_NAME

IGNORED_DIRS = {
    "node_modules", ".git", "dist", "build", "vendor",
    ".next", ".nuxt", ".venv", "venv", "__pycache__", "coverage", ".cache", ".idea",
    # Library and cache folders of other ecosystems. Generic names (target, out, env, deps, secrets) are left
    # out on purpose: in some repos they are product code.
    "Pods", "Carthage", "bower_components", ".gradle", ".terraform", ".tox", ".mypy_cache", ".pytest_cache",
    ".dart_tool",
}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "composer.lock", "go.sum", "Gemfile.lock",
}
# Generated code, plus secrets: the agent has no business reading credentials, and remote.py never uploads them.
IGNORED_FILE_PATTERNS = [
    "*.min.js", "*.min.css", "*.map", "*.generated.*", "*.pb.go", "*_pb2.py",
    ".env", ".env.*", "*.pem", "*.key", OVERVIEW_NAME,
    "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*", "*.kdbx",
    ".npmrc", ".pypirc", ".netrc", ".envrc", ".git-credentials", "*.tfstate", "*.tfstate.*", "*.tfvars",
    "credentials.json", "service-account*.json",
]


def is_ignored_dir(name: str) -> bool:
    """A folder the agent never enters and remote.py never uploads."""
    return name in IGNORED_DIRS


def is_ignored_name(name: str) -> bool:
    """A file the agent never reads and remote.py never uploads: lockfiles, generated code, secrets."""
    return name in LOCKFILES or any(fnmatch.fnmatch(name, pat) for pat in IGNORED_FILE_PATTERNS)
