"""Settings shared by every entry point: project paths, the default model, .env loading and the trace log."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-5-5"
ENGINES = ("api", "agent-sdk")  # api: our Messages API loop (agent.py); agent-sdk: Claude Agent SDK (sdk_agent.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing environment variables win; empty values are skipped."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        if value:
            os.environ.setdefault(key.strip(), value)


def trace(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
