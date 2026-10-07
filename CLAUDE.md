# Overview Agent (dev guide)

A Python CLI agent: `agent.py` runs a Claude API tool-use loop with sandboxed tools (`tools.py`) and writes
`PROJECT_OVERVIEW.md` (what a product does, not how) for some *other* repository.

## The runtime prompt is not for you
`prompts/overview_agent.md` is the system prompt the runtime agent receives. Treat it as source you edit, not as
instructions to follow. Its rules (only write PROJECT_OVERVIEW.md, Tier budgets, etc.) do not apply to
development work in this repo.

## Where responsibilities live
- `prompts/overview_agent.md`: what the model is told (goals, tiers, template, self-checks). Keep it short. No
  shell examples: the tool descriptions already explain usage.
- `tools.py`: rules enforced in code (ignore list, Tier 1/3 access, Tier 3 budget, single writable file,
  template headings, word limits) plus the `TOOLS` schemas the model sees.
- `agent.py`: CLI, `.env` loading, API loop, prompt caching, metrics.

Keep these in sync. If you change the template headings or word limit in the prompt, update `REQUIRED_HEADINGS`
and `MAX_WORDS`/`SOFT_WORDS` in `tools.py`, and the reverse.

## Commands
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env              # ANTHROPIC_API_KEY, model, limits, default repo
python agent.py <repo_path>       # live run, needs an API key
python3 -m unittest -v            # offline: test_tools.py (sandbox), test_agent.py (loop with fake client)
```

## Conventions
- Never commit `.env`.
- Don't remove either cache breakpoint in `run()` (the system block and the top-level `cache_control`).
  `test_agent.py` asserts both.
- Behavior or cost changes get a spec in `specs/` (Context / Change / Tests / Results), measured against a
  baseline commit using the run metrics.
- Experiments live in git worktrees under `.claude/worktrees/`. Don't edit them from main.
