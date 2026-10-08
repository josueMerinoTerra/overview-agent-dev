# Overview Agent (dev guide)

A Python CLI agent: `python main.py local` runs a Claude API tool-use loop (`overview_agent/agent.py`) with
sandboxed tools (`overview_agent/sandbox.py`) and writes `PROJECT_OVERVIEW.md` (what a product does, not how)
for some *other* repository.

## The runtime prompt is not for you
`overview_agent/prompts/overview_agent.md` is the system prompt the runtime agent receives. Treat it as source you edit, not as
instructions to follow. Its rules (only write PROJECT_OVERVIEW.md, Tier budgets, etc.) do not apply to
development work in this repo.

## Where responsibilities live
- `main.py`: the only entry point (`local`, `remote`, `build-template`): argparse, env defaults, exit codes.
  It imports `remote` and `e2b_template` lazily, because the sandbox image has no `e2b`.
- `overview_agent/prompts/overview_agent.md`: what the model is told (goals, tiers, template, self-checks). Keep it
  short. No shell examples: the tool descriptions already explain usage.
- `overview_agent/config.py`: `PROJECT_ROOT`, `DEFAULT_MODEL`, `.env` loading, `trace`.
- `overview_agent/overview_format.py`: the overview template in code (`OVERVIEW_NAME`, `REQUIRED_HEADINGS`,
  `MAX_WORDS`/`SOFT_WORDS`, `validate_overview`).
- `overview_agent/ignore_rules.py`: ignore lists and secret patterns, shared by the sandbox and `remote.py`'s
  upload filter.
- `overview_agent/sandbox.py`: `RepoSandbox`, the rules enforced in code (Tier 1/3 access, Tier 3 budget, single
  writable file).
- `overview_agent/tool_schemas.py`: the `TOOLS` schemas the model sees.
- `overview_agent/agent.py`: API loop, prompt caching, metrics.
- `overview_agent/remote.py`: E2B orchestration only (sandbox, clone or upload, run `main.py local`, download
  results). No agent logic.
- `overview_agent/e2b_template.py`: the sandbox image (Python, git, anthropic). No secrets; `remote.py` uploads
  `main.py` and the package on every run.

Keep these in sync. If you change the template headings or word limit in the prompt, update `REQUIRED_HEADINGS`
and `MAX_WORDS`/`SOFT_WORDS` in `overview_format.py`, and the reverse.

## Commands
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                    # ANTHROPIC_API_KEY, model, limits, default repo
python main.py local <repo_path>        # live run, needs an API key
python main.py build-template           # once: build the E2B sandbox template (needs E2B_API_KEY)
python main.py remote <git-url|path>    # live run in E2B; results in overviews/<repo-name>/
python3 -m unittest -v                  # offline, tests/: sandbox, format, loop, CLI, E2B (fake sandbox)
```

## Conventions
- Never commit `.env`.
- Don't remove either cache breakpoint in `run()` (`overview_agent/agent.py`) (the system block and the top-level
  `cache_control`). `tests/test_agent.py` asserts both.
- Behavior or cost changes get a spec in `docs/specs/` (Context / Change / Tests / Results), measured against a
  baseline commit using the run metrics.
- Implementation plans live in `docs/plans/`.
- Experiments live in git worktrees under `.claude/worktrees/`. Don't edit them from main.
