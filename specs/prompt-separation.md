# Spec: separate the runtime prompt from CLAUDE.md, and compact it

Status: implemented, live run pending · Baseline: `8ef3697`

## Context
`Claude.md` did two jobs. `agent.py` loaded it as the runtime agent's system prompt, and Claude Code (on a
case-insensitive filesystem) loaded it as the project CLAUDE.md in every development session. So the developing
agent got told things like "never modify any file except PROJECT_OVERVIEW.md".

The prompt also carried shell example commands that the runtime agent can't run. `agent.py`'s `RUNTIME_PREFACE`
existed mostly to map those examples to tools.

## Change
- `Claude.md` moves to `prompts/overview_agent.md`. `load_instructions()` reads that path directly.
- `RUNTIME_PREFACE` is removed. The parts still useful are folded into the prompt: there's no shell, the harness
  enforces the limits, and the harness appends the Tier 3 count.
- The prompt is compacted (about 950 → 490 words, including the old prefix):
  - Removed: both example-command blocks, the shell-to-tool mapping, the "running log" instruction (tier 3
    `read_file` already requires `question` and `reason`), and the "budget respected" self-check (enforced and
    reported by the harness).
  - Kept unchanged: the three questions, the tier order, the output template (validated by
    `REQUIRED_HEADINGS`) and the quality checks.
- A new dev-facing `CLAUDE.md` describes the repo, where each responsibility lives, commands and conventions.
- Prompt caching is unchanged: the same single system block with `cache_control`, plus the top-level breakpoint.

## Tests
- `test_agent.py` runs `agent.run()`, so it loads the prompt from the new path and checks both cache breakpoints.
- `test_tools.py`: unchanged.

## Measurement protocol (to do)
- Baseline `8ef3697` vs this change, 3 runs each, alternated, same target repo, model and limits.
- **Metrics:** turns, tool calls, tool errors, `first_write_ok`, cache-write and cache-read tokens, cost per run.
- **Success:** no regression in turns, errors or overview quality. The first turn still writes to the cache
  (`cache_creation_input_tokens > 0`), so the shorter prefix stays above the minimum cacheable length.
