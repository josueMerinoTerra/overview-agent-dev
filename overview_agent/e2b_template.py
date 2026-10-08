"""Build the E2B sandbox template that remote.py runs the agent in.

Run once, and again only when the agent's dependencies change:  python main.py build-template
The template is only the environment (Python, git, the anthropic SDK and the Claude Agent SDK). The agent code is
uploaded on every run, and no API key is ever baked in.
"""
from __future__ import annotations

from e2b import Template, default_build_logger

from overview_agent.config import load_dotenv
from overview_agent.remote import TEMPLATE

AGENT_SDK = "claude-agent-sdk==0.2.164"  # keep in sync with requirements.txt (a test checks)


def template():
    return (Template().from_python_image("3.12").apt_install("git")
            .pip_install("anthropic").pip_install(AGENT_SDK))


def build() -> None:
    load_dotenv()  # for E2B_API_KEY
    # 2 GB: the Agent SDK runs the bundled Claude Code CLI as a subprocess next to Python.
    Template.build(template(), TEMPLATE, cpu_count=1, memory_mb=2048, on_build_logs=default_build_logger())
    print("Built template %s" % TEMPLATE)
