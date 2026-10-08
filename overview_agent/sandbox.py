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

from overview_agent.ignore_rules import is_ignored_dir, is_ignored_name
from overview_agent.overview_format import OVERVIEW_NAME, validate_overview

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
        errors, warnings = validate_overview(content, self._cited_path_problem)
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

    def _cited_path_problem(self, token: str) -> Optional[str]:
        """A warning if a path cited as evidence is missing or off-limits, else None."""
        try:
            if not self._resolve(token.rstrip("/")).exists():
                return "cited path not found in repo: `%s` (remove it or fix the name)" % token
        except ToolError:
            return "cited path is off-limits or outside the repo: `%s`" % token
        return None

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
