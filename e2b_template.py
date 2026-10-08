#!/usr/bin/env python3
"""Build the E2B sandbox template that remote.py runs the agent in.

Run once, and again only when the agent's dependencies change:  python e2b_template.py
The template is only the environment (Python, git, the anthropic SDK). The agent code is uploaded on every run,
and no API key is ever baked in.
"""
from __future__ import annotations

from e2b import Template, default_build_logger

from agent import load_dotenv
from remote import TEMPLATE


def template():
    return Template().from_python_image("3.12").apt_install("git").pip_install("anthropic")


def main() -> None:
    load_dotenv()  # for E2B_API_KEY
    Template.build(template(), TEMPLATE, cpu_count=1, memory_mb=1024, on_build_logs=default_build_logger())
    print("Built template %s" % TEMPLATE)


if __name__ == "__main__":
    main()
