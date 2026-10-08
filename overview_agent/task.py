"""The task both engines give the model: the instructions, the first message and the one-time reminder."""
from __future__ import annotations

import sys
from pathlib import Path

from overview_agent.overview_format import OVERVIEW_NAME

FIRST_MESSAGE = (
    "Create %s for the repository at the sandbox root (paths are relative to it: '.'). "
    "Follow Stage 1, 2 and 3 of your instructions." % OVERVIEW_NAME
)
REMINDER = "You have not written %s yet. Call write_overview." % OVERVIEW_NAME


def load_instructions() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "overview_agent.md"
    if not path.is_file():
        sys.exit("error: prompts/overview_agent.md not found in the overview_agent package")
    return path.read_text(encoding="utf-8")
