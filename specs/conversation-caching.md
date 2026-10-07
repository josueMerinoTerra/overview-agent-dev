# Spec: cache the conversation, not just the system prompt

Status: implemented, measurement pending · Branch: `perf/conversation-caching` (from `820c313`)

## Context
The bash-tool benchmark (spec `specs/bash-tool-refactor.md` on branch `worktree-refactor-bash-tool`) measured
6 runs on terra-agents-backend. In both tool versions, **85–87% of input tokens went out uncached**, at about
$0.39 per run on `claude-sonnet-5-5`.

The cause: only the system block carries `cache_control`. Every turn re-sends the whole history (README reads,
listings, tool results), and that part is billed at full input price each time.

This lever is independent of the tool choice, so it is measured on the current custom tools.

## Change
`agent.py`, in the `client.messages.create(...)` call in `run()`:
- Add top-level automatic caching, `cache_control={"type": "ephemeral"}`. The breakpoint follows the last
  cacheable block, so turn N reads turns 1..N-1 from the cache, and only the newest turn is new input.
- Keep the existing system-block breakpoint (tools + instructions). That makes 2 breakpoints, under the limit of 4.
- Default 5-minute TTL. A run takes under a minute between turns, so a longer TTL is not needed.

Expected trade-off: cache writes cost 1.25× the input price and cache reads 0.1×. Each turn's new content is
written once and then read on every later turn, so the saving grows with the number of turns.

## Tests
- `test_agent.py`: a fake client captures every `messages.create` call and asserts that both breakpoints are sent.
- `test_tools.py`: unchanged.

## Measurement protocol
- **Target:** a fresh copy of terra-agents-backend for each run, without `.git` or the existing `PROJECT_OVERVIEW.md`.
- **Versions:** baseline `820c313` (system-only caching) vs this branch. 3 runs each, alternated, same model
  and limits.
- **Metrics:** the uncached share of input, cache-read and cache-write tokens, cost per run (Sonnet 5.5 list
  prices: $2 per M input, $10 per M output, $2.50 per M cache write, $0.20 per M cache read), turns, time, and
  overview quality side by side.
- **Success:** the uncached share falls well below 85% and cost per run drops by 30% or more, with turns and
  quality unchanged.

## Results
_Pending._
