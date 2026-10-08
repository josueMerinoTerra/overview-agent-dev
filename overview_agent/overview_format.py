"""The PROJECT_OVERVIEW.md template, enforced in code.

Keep REQUIRED_HEADINGS and the word limits in sync with the template in prompts/overview_agent.md.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

OVERVIEW_NAME = "PROJECT_OVERVIEW.md"

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


def validate_overview(
    content: str, cited_path_problem: Callable[[str], Optional[str]]
) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for an overview draft. Errors block the write; warnings are returned with it.

    `cited_path_problem(path)` checks one path cited under 'Evidence & confidence' and returns a warning, or None.
    """
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

    sections = _sections(content)
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
        problem = cited_path_problem(token)
        if problem:
            warnings.append(problem)
    return errors, warnings


def _sections(content: str) -> Dict[str, str]:
    parts = re.split(r"^## (.+?)\s*$", content, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
