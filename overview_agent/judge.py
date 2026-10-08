"""Scores one PROJECT_OVERVIEW.md against its repository, blind to which engine wrote it. Used by `main.py bench`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from overview_agent.recorder import USAGE_KEYS
from overview_agent.sandbox import RepoSandbox, ToolError

JUDGE_MODEL = "claude-sonnet-5-5"
CRITERIA = ("accuracy", "coverage", "what_not_how", "clarity")
RUBRIC = """You grade PROJECT_OVERVIEW.md files: product overviews that say what a product does, not how it is built.
You get the repository's file tree and its curated docs and manifests, then one overview.
Score each criterion from 1 (poor) to 5 (excellent) and give a one-sentence reason:
- accuracy: every claim is supported by the repository material; nothing is invented.
- coverage: it names the core user, the key features and the main workflow.
- what_not_how: it describes behavior and value, not implementation (frameworks, file layout, code details).
- clarity: a new teammate understands the product after one read.
Judge only what is written. Length and formatting are not criteria."""
SCHEMA = {
    "type": "object",
    "properties": {
        c: {
            "type": "object",
            "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["score", "reason"],
            "additionalProperties": False,
        }
        for c in CRITERIA
    },
    "required": list(CRITERIA),
    "additionalProperties": False,
}


class JudgeError(Exception):
    """The judge gave no usable verdict for one overview."""


def build_digest(repo: str) -> str:
    """The file tree plus every root-level and docs/ file the agents could read for free (Tier 1)."""
    sandbox = RepoSandbox(repo, log=lambda msg: None)
    parts = ["## File tree\n" + sandbox.list_tree(".", 2)]
    root = Path(repo)
    candidates: List[Path] = sorted(p for p in root.iterdir() if p.is_file())
    if (root / "docs").is_dir():
        candidates += sorted(p for p in (root / "docs").iterdir() if p.is_file())
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        try:
            parts.append("## %s\n%s" % (rel, sandbox.read_file(rel, tier=1)))
        except ToolError:  # not Tier 1, ignored or binary: the agents could not read it for free either
            continue
    return "\n\n".join(parts)


def judge(overview: str, digest: str, client) -> Dict[str, Any]:
    resp = client.messages.create(
        model=JUDGE_MODEL, max_tokens=4000,
        system=[
            {"type": "text", "text": RUBRIC},
            {"type": "text", "text": "# Repository material\n\n" + digest, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": "Score this overview.\n\n<overview>\n%s\n</overview>" % overview}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise JudgeError("judge stopped with stop_reason=%s" % resp.stop_reason)
    try:
        scores = json.loads("".join(b.text for b in resp.content if b.type == "text"))
        total = 0
        for c in CRITERIA:
            if not 1 <= int(scores[c]["score"]) <= 5:
                raise JudgeError("score out of range for %s: %s" % (c, scores[c]["score"]))
            total += int(scores[c]["score"])
    except (ValueError, KeyError, TypeError) as e:
        raise JudgeError("unreadable verdict: %s" % e)
    usage = {k: getattr(resp.usage, k, 0) or 0 for k in USAGE_KEYS}
    return {"model": JUDGE_MODEL, "scores": scores, "total": total, "usage": usage}
