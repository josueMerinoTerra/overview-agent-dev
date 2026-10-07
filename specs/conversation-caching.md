# Spec: cache the conversation, not just the system prompt

Status: implemented and measured. **−55% cost per run, no behaviour change** · Branch: `perf/conversation-caching` (from `820c313`)

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

## Results (2026-10-07)
6 live runs on fresh copies of terra-agents-backend, `claude-sonnet-5-5`, versions alternated.
Baseline `820c313` vs caching `209e282`. Total spend: $1.19 baseline + $0.47 caching.

Each cell is the median, with the [min–max] range in brackets.

| Metric | System-only cache (baseline) | Conversation cache | Change |
|---|---|---|---|
| Turns | 7 [7–7] | 7 [7–7] | 0% |
| Tool calls | 11 [11–12] | 11 [11–12] | 0% |
| Input tokens (all) | 195962 [195820–197157] | 196066 [191981–196580] | 0% |
| – uncached | 171665 [171523–172860] | 16 [16–16] | −100% |
| – cache write | 0 [0–3471] | 39410 [11220–39614] | |
| – cache read | 24297 [20826–24297] | 156950 [156640–180745] | |
| Uncached share of input | 88% | **0%** | −88 pts |
| Output tokens | 4351 [4326–4957] | 4596 [4549–4957] | +6% |
| **Cost per run** | **$0.392** [$0.391–$0.408] | **$0.176** [$0.114–$0.176] | **−55%** |
| Seconds | 34.9 [33.6–40.2] | 37.2 [37.0–38.3] | +7% |

- **Quality:** unchanged. All 6 overviews are valid, 579–639 words, with the same core user, features and
  Medium/Low ratings. The first `write_overview` was rejected in most runs of both versions (word count),
  which is a separate issue.
- **Agent behaviour:** identical. The same number of turns, tool calls and errors. Caching changes only billing.

**Verdict: success, and well past the 30% bar. Cost per run is cut by 55% ($0.39 → $0.18).**

Notes:
- The total token count is unchanged. Caching reprices input: about 80% of it is now read from the cache
  at 0.1×. Each turn's new content is written to the cache once, at 1.25×.
- The cheapest run ($0.114) reused cache entries left by the previous run of the same repo, within the
  5-minute TTL. A single cold run on a repo costs about $0.18.
- Wall time didn't improve (+7%, within noise for 3 runs). Most of the time goes to output generation,
  not reading input.

**Recommendation:** merge this branch on its own. The bash-tool question
(`specs/bash-tool-refactor.md`, branch `worktree-refactor-bash-tool`) should be re-measured against this cached
baseline, since every turn now costs less.
