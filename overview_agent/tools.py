"""Sandboxed repo tools for the Product Overview Agent.

The model decides *what* to look at; this module enforces the hard rules from
prompts/overview_agent.md in code, so a prompt slip can't break them:

  * nothing is read inside node_modules/.git/dist/build/vendor (and other library folders), lockfiles,
    generated code or secrets (.env, keys, keystores, credentials files)
  * Tier 1 reads are limited to curated docs + manifests
  * Tier 3 reads are capped at 5 distinct files and 120 lines each
  * the only file that can ever be written is <repo>/PROJECT_OVERVIEW.md
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

OVERVIEW_NAME = "PROJECT_OVERVIEW.md"

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
MANIFESTS = {
    "package.json", "pyproject.toml", "setup.cfg", "setup.py", "Cargo.toml",
    "composer.json", "go.mod", "Gemfile", "pom.xml",
}
DOCS_INDEX_STEMS = {"index", "readme", "overview", "introduction", "intro", "about", "getting-started"}

TIER1_MAX_LINES = 300
TIER3_MAX_LINES = 120
TIER3_MAX_FILES = 5
TREE_MAX_ENTRIES = 300
MAX_SCAN_FILES = 20000
MAX_GREP_FILE_BYTES = 1_000_000

REQUIRED_HEADINGS = [
    "In one sentence",
    "Problem & core user",
    "Key features",
    "Main user workflow",
    "Evidence & confidence",
    "Open questions",
]
MAX_WORDS = 700  # the prompt says "under ~600"; 600-700 passes with a warning.
SOFT_WORDS = 600


def is_ignored_dir(name: str) -> bool:
    """A folder the agent never enters and remote.py never uploads."""
    return name in IGNORED_DIRS


def is_ignored_name(name: str) -> bool:
    """A file the agent never reads and remote.py never uploads: lockfiles, generated code, secrets."""
    return name in LOCKFILES or any(fnmatch.fnmatch(name, pat) for pat in IGNORED_FILE_PATTERNS)


class ToolError(Exception):
    """A problem the model can read and recover from (returned as is_error)."""


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


class RepoSandbox:
    def __init__(self, root: str, log: Callable[[str], None] = _stderr) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ToolError("repository path is not a directory: %s" % root)
        self.log = log
        self.tier3: Dict[str, int] = {}  # relpath -> question it answered (insertion ordered)
        self.overview_written = False

    # ------------------------------------------------------------------ paths
    def _rel_ignored(self, rel: Path) -> bool:
        parts = rel.parts
        if any(is_ignored_dir(p) for p in parts):
            return True
        return bool(parts) and is_ignored_name(parts[-1])

    def _resolve(self, rel: str) -> Path:
        """Resolve a repo-relative path, refusing escapes and ignored locations."""
        target = (self.root / (rel or ".")).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError("path escapes the repository: %s" % rel)
        if target != self.root and self._rel_ignored(target.relative_to(self.root)):
            raise ToolError("path is off-limits (ignored/generated/secret): %s" % rel)
        return target

    def _walk(self, start: Path):
        """Yield (dirpath, dirnames, filenames) with ignored entries pruned, in stable order."""
        for dirpath, dirnames, filenames in os.walk(str(start)):
            dirnames[:] = sorted(
                d for d in dirnames if not is_ignored_dir(d) and not d.startswith(".git")
            )
            filenames = sorted(f for f in filenames if not is_ignored_name(f))
            yield Path(dirpath), dirnames, filenames

    def _relposix(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # -------------------------------------------------------- names-only tools
    def list_tree(self, path: str = ".", max_depth: int = 2) -> str:
        start = self._resolve(path)
        if not start.is_dir():
            raise ToolError("not a directory: %s" % path)
        max_depth = max(1, min(int(max_depth), 3))
        lines: List[str] = []
        base_depth = len(start.parts)
        for dirpath, dirnames, filenames in self._walk(start):
            depth = len(dirpath.parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            indent = "  " * depth
            if depth > 0:
                lines.append("%s%s/" % ("  " * (depth - 1), dirpath.name))
            if depth < max_depth:
                lines.extend("%s%s" % (indent, f) for f in filenames)
            if len(lines) >= TREE_MAX_ENTRIES:
                lines.append("... truncated at %d entries; narrow `path` or lower `max_depth`" % TREE_MAX_ENTRIES)
                break
        return "\n".join(lines) or "(empty)"

    def find_files(self, glob: str, limit: int = 40) -> str:
        limit = max(1, min(int(limit), 40))
        pat = glob.lower()
        hits: List[str] = []
        scanned = 0
        for dirpath, _, filenames in self._walk(self.root):
            for f in filenames:
                scanned += 1
                rel = self._relposix(dirpath / f)
                if fnmatch.fnmatch(rel.lower(), pat):
                    hits.append(rel)
                    if len(hits) >= limit:
                        return "\n".join(hits) + "\n... limit reached; refine the glob"
            if scanned > MAX_SCAN_FILES:
                break
        return "\n".join(hits) or "(no matches)"

    def grep(self, pattern: str, glob: str = "*", limit: int = 30) -> str:
        """Return only the matched fragments (like grep -o), never whole lines or files."""
        limit = max(1, min(int(limit), 30))
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError("invalid regex: %s" % e)
        gpat = glob.lower()
        out: List[str] = []
        scanned = 0
        for dirpath, _, filenames in self._walk(self.root):
            for f in filenames:
                full = dirpath / f
                rel = self._relposix(full)
                if not fnmatch.fnmatch(rel.lower(), gpat):
                    continue
                scanned += 1
                if scanned > 5000:
                    return "\n".join(out) + "\n... scan limit reached; narrow `glob`"
                try:
                    if full.stat().st_size > MAX_GREP_FILE_BYTES or self._is_binary(full):
                        continue
                    with open(str(full), encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            for m in rx.finditer(line):
                                out.append("%s:%d: %s" % (rel, lineno, m.group(0)[:200]))
                                if len(out) >= limit:
                                    return "\n".join(out) + "\n... limit reached"
                except OSError:
                    continue
        return "\n".join(out) or "(no matches)"

    @staticmethod
    def _is_binary(p: Path) -> bool:
        with open(str(p), "rb") as fh:
            return b"\0" in fh.read(4096)

    # ------------------------------------------------------------ content reads
    def _tier1_eligible(self, rel: Path) -> bool:
        parts = rel.parts
        name = parts[-1]
        if name.upper().startswith(("README", "CONTRIBUTING")):
            return True
        if len(parts) == 1:
            return name in MANIFESTS or name.lower().endswith(".md")
        if parts[0].lower() == "docs" and len(parts) <= 3:
            return Path(name).stem.lower() in DOCS_INDEX_STEMS
        return False

    def read_file(self, path: str, tier: int, question: Optional[int] = None, reason: str = "") -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError("not a file: %s" % path)
        if self._is_binary(target):
            raise ToolError("binary file refused: %s" % path)
        rel = self._relposix(target)
        tier = int(tier)

        # A Tier-1-eligible file is always served as Tier 1: it is free, so never burn Tier 3 on it.
        if self._tier1_eligible(target.relative_to(self.root)):
            return self._read_head(target, rel, TIER1_MAX_LINES, "tier 1")
        if tier == 1:
            raise ToolError(
                "%s is not curated documentation or a manifest, so it is not a Tier 1 file. "
                "Tier 1 = README*, CONTRIBUTING*, top-level *.md, docs/ index files, project manifests. "
                "Use tier 3 only if a specific question (1, 2 or 3) is still unanswered." % rel
            )
        if tier != 3:
            raise ToolError("tier must be 1 or 3")

        try:
            q = int(question)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            q = 0
        if q not in (1, 2, 3):
            raise ToolError("tier 3 requires `question` = 1, 2 or 3 (which of the three questions you are escalating for)")
        if not reason or not reason.strip():
            raise ToolError("tier 3 requires a non-empty `reason` explaining the gap Tiers 1-2 left")
        if rel not in self.tier3 and len(self.tier3) >= TIER3_MAX_FILES:
            raise ToolError(
                "Tier 3 budget exhausted (%d/%d files: %s). Write the overview with what you have "
                "and list the remaining gaps under 'Open questions'."
                % (len(self.tier3), TIER3_MAX_FILES, ", ".join(self.tier3))
            )
        self.tier3.setdefault(rel, q)
        self.log("[tier 3 %d/%d] %s -> question %d (%s)" % (len(self.tier3), TIER3_MAX_FILES, rel, q, reason.strip()))
        return self._read_head(target, rel, TIER3_MAX_LINES, "tier 3 %d/%d" % (len(self.tier3), TIER3_MAX_FILES))

    @staticmethod
    def _read_head(target: Path, rel: str, max_lines: int, label: str) -> str:
        lines: List[str] = []
        more = False
        with open(str(target), encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    more = True
                    break
                lines.append(line.rstrip("\n"))
        header = "# %s (%s, first %d lines%s)" % (rel, label, len(lines), ", truncated" if more else "")
        return header + "\n" + "\n".join(lines)

    # -------------------------------------------------------------------- write
    def write_overview(self, content: str) -> str:
        errors, warnings = self._validate_overview(content)
        if errors:
            raise ToolError(
                "PROJECT_OVERVIEW.md was NOT written. Fix these and call write_overview again:\n- "
                + "\n- ".join(errors)
            )
        (self.root / OVERVIEW_NAME).write_text(content.rstrip() + "\n", encoding="utf-8")
        self.overview_written = True
        msg = "Wrote %s (%d words). Tier 3 files used: %d/%d." % (
            OVERVIEW_NAME, len(content.split()), len(self.tier3), TIER3_MAX_FILES,
        )
        if warnings:
            msg += "\nWarnings (fix and rewrite if they point at a real problem):\n- " + "\n- ".join(warnings)
        return msg

    def _validate_overview(self, content: str):
        errors: List[str] = []
        warnings: List[str] = []

        first = next((l for l in content.splitlines() if l.strip()), "")
        if not re.match(r"^# .+: Product Overview\s*$", first):
            errors.append("first line must be '# <Project name>: Product Overview'")

        headings = re.findall(r"^## (.+?)\s*$", content, flags=re.M)
        if headings != REQUIRED_HEADINGS:
            errors.append(
                "the '## ' headings must be exactly, in order: %s (found: %s)"
                % (REQUIRED_HEADINGS, headings)
            )
            return errors, warnings  # section checks below depend on the headings

        sections = self._sections(content)
        words = len(content.split())
        if words > MAX_WORDS:
            errors.append("%d words; must be under ~600 (hard limit %d). Cut it down." % (words, MAX_WORDS))
        elif words > SOFT_WORDS:
            warnings.append("%d words; target is under ~600." % words)

        bullets = [l for l in sections["Key features"].splitlines() if re.match(r"^[-*] ", l)]
        if not bullets:
            errors.append("'Key features' needs bullet points ('- feature — what it lets the user do')")
        elif len(bullets) > 7:
            errors.append("'Key features' has %d bullets; maximum is 7" % len(bullets))
        elif len(bullets) < 3:
            warnings.append("'Key features' has %d bullets (expected 3-7); acceptable only if the repo gives no more evidence" % len(bullets))

        steps = [l for l in sections["Main user workflow"].splitlines() if re.match(r"^\d+[.)] ", l)]
        if not steps:
            errors.append("'Main user workflow' must be a numbered list of user steps")
        elif len(steps) < 3:
            warnings.append("'Main user workflow' has %d steps; confirm that is the whole journey" % len(steps))

        if not sections["Open questions"].strip():
            errors.append("'Open questions' must not be empty (write 'None' only if truly nothing is unclear)")
        if len(re.findall(r"\b(?:High|Medium|Low)\b", sections["Evidence & confidence"])) < 3:
            errors.append("'Evidence & confidence' needs a High/Medium/Low rating for each of: problem & core user, key features, main workflow")

        # Grounding: every file cited in backticks under Evidence should exist.
        for token in sorted(set(re.findall(r"`([^`\n]+)`", sections["Evidence & confidence"]))):
            if not re.search(r"[/.]", token) or re.search(r"[*?\[\s]", token):
                continue
            try:
                if not self._resolve(token.rstrip("/")).exists():
                    warnings.append("cited path not found in repo: `%s` (remove it or fix the name)" % token)
            except ToolError:
                warnings.append("cited path is off-limits or outside the repo: `%s`" % token)
        return errors, warnings

    @staticmethod
    def _sections(content: str) -> Dict[str, str]:
        parts = re.split(r"^## (.+?)\s*$", content, flags=re.M)
        return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}

    # ----------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict) -> str:
        try:
            if name == "list_tree":
                return self.list_tree(args.get("path", "."), args.get("max_depth", 2))
            if name == "find_files":
                return self.find_files(args["glob"], args.get("limit", 40))
            if name == "grep":
                return self.grep(args["pattern"], args.get("glob", "*"), args.get("limit", 30))
            if name == "read_file":
                return self.read_file(args["path"], args["tier"], args.get("question"), args.get("reason", ""))
            if name == "write_overview":
                return self.write_overview(args["content"])
        except KeyError as e:
            raise ToolError("missing required argument: %s" % e)
        raise ToolError("unknown tool: %s" % name)


TOOLS = [
    {
        "name": "list_tree",
        "description": (
            "List file and folder NAMES (never contents) under a directory, up to 3 levels deep. "
            "Ignored folders, lockfiles and generated files are already excluded. Use for the Tier 1 "
            "top-level layout and Tier 2 feature/domain folders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory relative to the repo root. Default '.'"},
                "max_depth": {"type": "integer", "description": "1-3. Default 2"},
            },
        },
    },
    {
        "name": "find_files",
        "description": (
            "Find file NAMES whose repo-relative path matches a case-insensitive glob such as "
            "'*route*', '*model*', '*migration*', '*.spec.*', '*pages*'. Returns up to 40 paths, no contents. Tier 2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "glob": {"type": "string"},
                "limit": {"type": "integer", "description": "max results, up to 40"},
            },
            "required": ["glob"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Regex search returning ONLY the matched fragments with file:line (like grep -o), up to 30. "
            "Intended for Tier 2 signals such as test titles: pattern \"describe\\(['\\\"][^'\\\"]+\" "
            "with glob '*.test.*'. Do not use it to read code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "description": "path glob filter, default '*'"},
                "limit": {"type": "integer"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the start of a file. Tier 1 (free): README*, CONTRIBUTING*, top-level *.md, docs/ index files, "
            "and manifests (package.json, pyproject.toml, ...), first 300 lines. Tier 3 (budgeted): any other "
            "non-ignored text file, first 120 lines, at most 5 distinct files for the whole run; requires `question` "
            "(1=purpose & core user, 2=key features, 3=main workflow) and a `reason` naming the gap Tiers 1-2 left. "
            "Do not use Tier 3 to read entry points like index.js/main.py."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "tier": {"type": "integer", "enum": [1, 3]},
                "question": {"type": "integer", "enum": [1, 2, 3], "description": "Required for tier 3"},
                "reason": {"type": "string", "description": "Required for tier 3"},
            },
            "required": ["path", "tier"],
        },
    },
    {
        "name": "write_overview",
        "description": (
            "Write PROJECT_OVERVIEW.md at the repo root (the only file you may write). The content is validated "
            "against the required template; on failure nothing is written and the errors are returned so you can fix "
            "and call again. Call this when all three questions are answered or the budget is spent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "Full markdown of the overview"}},
            "required": ["content"],
        },
    },
]
