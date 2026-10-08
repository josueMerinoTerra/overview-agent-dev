"""The tool definitions (JSON schemas) the model sees. sandbox.py enforces the rules behind them."""
from __future__ import annotations

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
