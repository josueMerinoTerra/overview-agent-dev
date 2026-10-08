# Spec: a second engine on the Claude Agent SDK, benchmarked against the API loop

Status: implemented and measured · Baseline: `0c57003` · Plan: `docs/plans/2026-10-08-agent-sdk-engine.md` · Step 2 of 2 (step 1:
`docs/specs/e2b-remote-run.md`)

## Context
Today the agent is a hand-written Messages API loop (`overview_agent/agent.py`): we own the loop, the tool
dispatch, the reminder, the cache breakpoints and the metrics. The goal of this step is **learning the other way
to build an agent**: the Claude Agent SDK (`claude-agent-sdk`), which is Claude Code packaged as a library and
supplies the loop for us. Then measure what each approach costs and how each one behaves on the same task.

Decisions made while brainstorming:
- **Side by side, not a replacement.** A second engine behind `--engine api|agent-sdk`, so both can run on the
  same repo on the same day. The loser can be deleted once the results are in.
- **Same task, same rules.** Both engines get the same prompt, the same `RepoSandbox` tools and the same limits.
  The rules enforced in code stay in `RepoSandbox`; the Agent SDK only calls them.
- **Locked down and isolated.** No Claude Code built-in tools, and nothing from the machine's Claude Code setup
  (`~/.claude`, plugins, memory, MCP connectors) reaches the runtime agent.
- **Benchmark both environments** (local and E2B), **as cheaply as possible**, on **terra-agents-backend only**.
- **Quality is judged by an LLM judge** with a fixed rubric, spot-checked by a human.

Agent SDK facts this design relies on (code.claude.com/docs/en/agent-sdk, `claude-agent-sdk` 0.2.164, checked
2026-10-08; confirm signatures against the installed package before coding):
- `pip install claude-agent-sdk` bundles the Claude Code CLI. No Node.js. Python 3.10+.
- Custom tools: `@tool(name, description, input_schema)` on an `async def f(args) -> {"content": [...],
  "is_error": bool}`, served in-process by `create_sdk_mcp_server(...)`. The model sees `mcp__<key>__<name>`,
  where `<key>` is the key in `mcp_servers`.
- `tools=[]` removes every built-in tool. `allowed_tools` only auto-approves. `permission_mode="dontAsk"` denies
  anything not pre-approved without prompting.
- `system_prompt="<string>"` replaces the default prompt entirely.
- `setting_sources` defaults to user + project + local. Auto memory, `~/.claude.json` and claude.ai connectors load
  regardless; turning them off takes `strict_mcp_config=True` and `env` (`CLAUDE_CONFIG_DIR`,
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `ENABLE_CLAUDEAI_MCP_SERVERS=false`).
- Tool search is on by default and defers MCP tool schemas.
- `max_turns` and `max_budget_usd` exist. There is no max-output-tokens option.
- `ResultMessage` carries `subtype`, `num_turns`, `usage` (input, output, cache write, cache read),
  `total_cost_usd` (a client-side estimate), `duration_ms`. Python `query()` raises after an error result.
- Prompt caching is automatic; breakpoints can't be placed by hand.
- `ClaudeSDKClient` keeps one session across several `query()` calls.

## Change

### New: `overview_agent/sdk_agent.py` (the `agent-sdk` engine)
`run(root, model, max_turns, metrics_json="") -> int`, the same contract as `agent.run` minus `max_tokens`.

- **Tools.** One `@tool` wrapper per entry in `TOOLS` (`tool_schemas.py`), built from its `name`, `description`
  and `input_schema`, so the schemas exist once. Each wrapper calls `sandbox.call(name, args)` through the shared
  `ToolRecorder` (below). A `ToolError` becomes `is_error: True` with the error text; any other exception does
  too, with the same message format as the `api` engine. Served as `mcp_servers={"overview": server}`.
- **Options** (`ClaudeAgentOptions`):
  - `system_prompt=load_instructions()` (the same `prompts/overview_agent.md`, about 3.5 KB)
  - `model`, `max_turns`, `max_budget_usd=0.50`, `effort="high"` (the Messages API default for
    `claude-sonnet-5-5`, which the `api` engine gets by not setting it)
  - `tools=[]`, `allowed_tools` = the five tool names spelled out (`mcp__overview__list_tree`, …),
    `permission_mode="dontAsk"`
  - `setting_sources=[]`, `strict_mcp_config=True`
  - `env`: `CLAUDE_CONFIG_DIR=<fresh temp dir>`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
    `ENABLE_CLAUDEAI_MCP_SERVERS=false`, `ENABLE_TOOL_SEARCH=false`
  - `cwd=<the same temp dir>`, never the target repo

  Tool search is off so both engines see every tool schema from turn 1 (`ENABLE_TOOL_SEARCH=false`, confirmed in
  the bundled CLI).
- **Flow.** `ClaudeSDKClient`: send the same first user message as the `api` engine, read until the
  `ResultMessage`. If `sandbox.overview_written` is still false, send the same one-time reminder and read again.
- **Metrics.** The same keys as the `api` engine (`engine` included), plus `sdk_cost_usd`. `turns` and the four
  token counts are summed over the one or two `ResultMessage`s. `wall_seconds` is measured by us, as in
  `agent.py`. Recorder fields come from the shared `ToolRecorder`. Two counts only this engine can produce:
  `schema_rejected_calls` (tool calls the SDK's own schema check answered before they reached `RepoSandbox`) and
  `denied_calls` (`permission_denials`). The `api` engine reports both as 0.
- **Limits and errors.** Hitting `error_max_turns` or `error_max_budget_usd` prints a warning naming the limit and
  skips the reminder; as in the `api` engine, the exit code then depends on whether the overview was written. Any
  other `error_*` subtype, or a `ClaudeSDKError`, prints one `error: …` line and exits 1. Metrics are written in
  every case.

Known differences, recorded rather than fixed:
- The prompt says `write_overview`; this engine's model sees `mcp__overview__write_overview`. The prompt is left
  identical for both engines.
- `--max-tokens` doesn't apply to this engine (no such option).
- Caching is the SDK's own, not our two breakpoints.
- `max_turns` applies per query in the SDK, so the reminder (when it fires) gets a fresh limit; in the `api`
  engine it covers the whole run.
- `thinking` is left at each side's default (the Messages API's for `claude-sonnet-5-5`, the CLI's for the SDK).
  `effort` is pinned to `high` on both.

Accounting rules (from the final review, checked against the bundled CLI):
- The `ResultMessage` `usage`, `model_usage` and `total_cost_usd` are cumulative for the session, so the last
  result replaces earlier ones; `num_turns` and `permission_denials` are per query and are summed.
- `tool_calls` and `tool_errors` include tool calls the SDK answered itself (bad arguments, unknown tool) and
  denied calls, since in the `api` engine the same calls reach `RepoSandbox` and fail there.
- `model_usage` is saved in the metrics, to show whether the CLI used any model besides `model` (the bench prices
  every token at the `model`'s rates).
- `CLAUDE*` variables in our process (for example from a Claude Code session that started the bench) are hidden
  while the SDK starts its CLI, because the SDK passes the whole environment through.

### New: `overview_agent/task.py` and `overview_agent/recorder.py`
What both engines share moves out of `agent.py` (no module may import `agent` except `main.py`):
- `task.py`: `load_instructions()`, the first user message and the reminder.
- `recorder.py`: `finish_run(...)`, the end-of-run summary, metrics file and exit code, and `ToolRecorder`.

`agent.py` counts tool calls inline today. That code moves into `ToolRecorder`, which both engines use, so behavior
counts are computed by the same code:
- `call(name, args) -> (output, is_error)`, which wraps `sandbox.call` and catches errors as `agent.py` does
  today
- counters: `tool_calls`, `tool_errors`, `write_attempts`, `first_write_ok` (as today), plus
  `duplicate_calls` (same tool with the same arguments as an earlier call in the run, compared via sorted JSON)
  and `rejected_calls` (a `ToolError` from `RepoSandbox`: ignored file, Tier 3 budget, invalid overview)

`agent.py` keeps its loop, both cache breakpoints and its output, and adds `engine: "api"` plus the two new
counters to its metrics.

### `main.py`
- `local` and `remote` get `--engine api|agent-sdk` (default `api`, env `OVERVIEW_ENGINE`). `local` imports
  `sdk_agent` lazily, only for `agent-sdk`. `remote` forwards the flag to the `main.py local` it runs.
- New `bench` subcommand (below).

### `overview_agent/e2b_template.py`
Add `pip_install("claude-agent-sdk==0.2.164")`, the same pin as `requirements.txt` (a test checks), and raise the
sandbox to 2 GB RAM, since the SDK runs the bundled Claude Code CLI next to Python. Rebuild once with
`python main.py build-template`. The sandbox runs as a normal user, and `dontAsk` doesn't need root.

### New: `overview_agent/judge.py`
`judge(overview, digest, client) -> dict`: one Messages API call that scores one overview.
- Model `claude-sonnet-5-5`, structured output (`output_config.format`) with a 1-5 score and a one-line reason for
  **accuracy** (claims match the repo), **coverage** (core user, key features, main workflow), **what-not-how**
  (no implementation detail), and **clarity**.
- The digest gives the judge enough to check claims: the file tree from `RepoSandbox.list_tree` plus the README
  and manifests (the Tier 1 files). It goes in a cached system block, so every overview of the repo after the
  first reads it from cache.
- The judge is blind: it never sees the engine or the environment, and overviews are judged in shuffled order.

### New: `overview_agent/bench.py` and `main.py bench <repo_path>`
Runs the matrix, judges the results and writes the report. Results go to `bench/<timestamp>/` (git-ignored).

- **Matrix (8 runs).** For each engine: 1 local run, then 3 E2B runs, all on the same repo. The local runs come
  first and are the pilot: the bench stops at the first failed run.
- **Order and caching.** An engine's runs go back to back, so its first run is the cold one and the rest read
  that run's prompt cache, which lives on Anthropic's side and is shared by local and E2B runs. The two engines
  never share a cache entry (different tools and framing), so this is fair. The report marks the cold run.
- **Inputs.** The repo goes through the `remote.py` filter (`make_tarball`) for both environments. Local runs
  extract it into a fresh temp copy, so the real folder is never written and both environments see the same
  files (except a symlink pointing outside the repo, which the local copy skips instead of crashing on). E2B runs use `remote.run_remote` with the local path.
- **Per run:** `bench/<ts>/<env>/<engine>/run-N/` holds `PROJECT_OVERVIEW.md`, `metrics.json`, the trace
  (`trace.log`), `judge.json`, and for E2B the end-to-end seconds.
- **Resume.** A run that finished with exit code 0 (recorded in its `bench.json`) is skipped, so a crash never
  pays for the same run twice. A failed run is re-run; its earlier attempt is kept as `run-N.failed-<time>` and
  its cost still counts toward the spend guard. Pass the same `--out bench/<ts>` to resume.
- **Spend guard.** Before each run, the bench prices what it has spent so far and stops if the total would pass
  `--max-usd` (default 3.00).
- **Pricing.** Sonnet 5.5 list prices per M tokens: $2 input, $10 output, $2.50 cache write, $0.20 cache read (as in
  `conversation-caching.md`). Both engines are priced from their token counts. The SDK's `total_cost_usd` is kept
  for comparison only.
- **Report.** `summary.md`: for each environment and engine, median and [min-max] of turns, tool calls, tool
  errors, duplicate calls, rejected calls, write attempts, `first_write_ok`, uncached input, cache write, cache
  read, output tokens, cost (cold run and warm runs separately), agent seconds, end-to-end seconds (E2B), and the
  four judge scores. It then shows, side by side, the `api` and `agent-sdk` overviews with the largest gap in judge
  score, for the human spot check.

### Small edits
- **Python 3.12.** `claude-agent-sdk` needs Python 3.10+, and the project venv was Python 3.9.6, so the venv is
  recreated on Python 3.12 (Homebrew `python@3.12`).
- `requirements.txt`: add `claude-agent-sdk==0.2.164`.
- `.env.example`: add `OVERVIEW_ENGINE=api`.
- `.gitignore`: add `bench/`.
- `README.md`: an "Engines" section (what each one is, the flag, what `bench` does and roughly what it costs).
- `CLAUDE.md`: under "Where responsibilities live", add `sdk_agent.py`, `recorder.py`, `judge.py` and `bench.py`,
  and the `--engine` and `bench` commands. Note that the cache-breakpoint rule applies to the `api` engine only.

### Unchanged
The runtime prompt, the tool schemas, `RepoSandbox` and its rules, the ignore lists, and the `api` engine's request
(both cache breakpoints). `remote.py` changes only to forward `--engine`.

### Security notes
- In the `agent-sdk` engine, the model can call only the five `RepoSandbox` tools. Built-ins are removed
  (`tools=[]`), and anything else is denied (`dontAsk`). This matters most for local runs, where a built-in like
  Bash would act on the real machine.
- Local runs never load the developer's Claude Code setup: no settings, CLAUDE.md, plugins, memory or MCP servers.
- The E2B runs upload a filtered copy of terra-agents-backend (a private repo) to E2B, as `remote.py` does for any
  local folder.

## Tests
All offline, with fakes:
- `tests/test_sdk_agent.py`, with a fake `ClaudeSDKClient`:
  - every lockdown and isolation option is set as above, the system prompt is the prompt file, and `cwd` is not
    the repo
  - one wrapper per `TOOLS` entry, with the same name and schema
  - a `ToolError` becomes `is_error: True`
  - metrics have the same keys as the `api` engine, plus `engine`
  - the reminder is sent once when nothing was written, and not at all otherwise
  - an `error_max_turns` result exits 1 and still writes metrics
- `tests/test_recorder.py`: duplicate detection ignores argument order and counts only repeats; a rejected call
  counts as both an error and a rejection; `first_write_ok` as today.
- `tests/test_judge.py`: the request uses the cached digest block and structured output; the parsed scores come
  back; the engine name never appears in the request.
- `tests/test_bench.py`: the matrix and its order; resume skips finished runs; the spend guard stops before going
  over `--max-usd`; pricing; median and [min-max]; local inputs go to a temp copy, never the source path.
- `tests/test_main.py`: `--engine` reaches `local`, and `remote` forwards it; `agent-sdk` is imported only when
  chosen.
- `tests/test_agent.py`: unchanged assertions pass, both cache breakpoints included, plus the new metric keys.

## Measurement protocol
- **Target:** `/Users/josue.merino/Projects/terra-agents-backend`, filtered as above (no `.git`, no existing
  `PROJECT_OVERVIEW.md`).
- **Runs:** `python main.py bench <path>`: 8 runs, `claude-sonnet-5-5`, `--max-turns 25`, default
  `--max-tokens` for the `api` engine.
- **Budget:** about $2 expected (8 runs at about $0.10-0.25, plus 8 judge calls at a few cents); hard stop at $3.
- **Questions this answers:**
  1. Cost and tokens: does the Agent SDK's framing cost more per run, and does caching absorb it?
  2. Speed: agent seconds, plus E2B overhead.
  3. Behavior: turns, tool calls, duplicate and rejected calls, first-write success, and how much runs vary.
  4. Quality: judge scores and the human spot check.
  5. Environment: do local and E2B runs of the same engine behave alike (turns, calls, total input tokens)?
- **Success** for this spec is a complete, trustworthy comparison, not one engine winning: all 8 runs finish or
  fail with a recorded reason, the judge's scores agree with the spot check, and the Results section states which
  engine is better on each question and by how much.

## Results (2026-10-08)
`python main.py bench /Users/josue.merino/Projects/terra-agents-backend --out bench/2026-10-08-terra`, run at
`b7429fc`: 8 runs, all finished, all judged. `claude-sonnet-5-5`, `--max-turns 25`. Total spend **$1.26** (agents
and judge), plus a $0.04 local `agent-sdk` pilot on `dayNight`. Each cell is the median with the [min–max]
range. The local column is each engine's single, cold-cache run.

| Metric | local · api | local · agent-sdk | E2B · api | E2B · agent-sdk |
|---|---|---|---|---|
| Turns | 6 | 14 | 6 [5–6] | 15 [12–16] |
| Tool calls | 7 | 13 | 8 [6–9] | 14 [11–15] |
| Tool errors | 2 | 2 | 2 [1–2] | 1 [1–2] |
| Duplicate calls | 0 | 0 | 0 [0–0] | 0 [0–0] |
| Rejected by RepoSandbox | 2 | 2 | 2 [1–2] | 1 [1–2] |
| Answered by the SDK (bad args, unknown tool) | 0 | 0 | 0 [0–0] | 0 [0–0] |
| Denied (permission) | 0 | 0 | 0 [0–0] | 0 [0–0] |
| Write attempts | 2 | 2 | 2 [2–2] | 2 [2–2] |
| Input, uncached | 14 | 18 | 14 [12–14] | 18 [14–18] |
| Cache write | 35911 | 26182 | 16317 [10404–34676] | 26502 [20289–26977] |
| Cache read | 122834 | 125021 | 119281 [105336–159411] | 123606 [74728–132310] |
| Output tokens | 4332 | 5123 | 4311 [4242–4616] | 5339 [5051–5379] |
| Cost (USD) | $0.158 | $0.142 | $0.119 [$0.093–$0.150] | $0.145 [$0.116–$0.147] |
| Agent seconds | 32.5 | 42.0 | 32.1 [31.0–33.6] | 50.6 [42.2–54.2] |
| End-to-end seconds | 33.7 | 43.6 | 44.9 [44.0–45.2] | 64.3 [55.6–70.3] |
| Judge: accuracy | 4 | 3 | 4 [4–4] | 3 [3–4] |
| Judge: coverage | 4 | 3 | 5 [4–5] | 3 [3–4] |
| Judge: what-not-how | 4 | 4 | 4 [4–4] | 4 [3–4] |
| Judge: clarity | 4 | 4 | 4 [4–5] | 4 [3–4] |
| Judge: total (of 20) | 16 | 14 | 17 [16–18] | 14 [12–16] |
| First write accepted | 0/1 | 0/1 | 0/3 | 0/3 |

**The main finding is a harness effect, not a model effect.** The Claude Code CLI under the Agent SDK keeps an MCP
tool result inline only up to 50,000 characters (`rF=50000` in the bundled CLI 2.1.292). Above that it saves the
result to a file and shows the model a preview. Terra's `README.md` comes back from `read_file` (Tier 1, 300 lines)
as 55,523 characters, so the `agent-sdk` agent never saw most of it, including the Client Pulse tool table at line
271. One run says so itself: "The README was too large to read in full, so I only saw its first part and its
headings." The `api` engine sends the full 55 KB. Every difference below traces back to this.

- **Cost:** about even. Cold: `agent-sdk` $0.142 vs `api` $0.158. Warm (E2B): `agent-sdk` $0.145 vs `api` $0.119,
  so `agent-sdk` costs **22% more** per warm run. It has more turns but a smaller README in context, so each turn
  is cheaper. Caching works in both: uncached input is 12–18 tokens per run. The CLI also makes one small Haiku 4.5
  call per run (about 900 input tokens, about $0.001). It is in `model_usage` and `sdk_cost_usd` but not in the
  bench's cost column.
- **Speed:** `api` is faster. Agent time is 32 s vs 42–51 s (**+57%** on E2B medians), and end-to-end on E2B is
  45 s vs 64 s.
- **Behavior:** `agent-sdk` takes **2.5× the turns** (14–15 vs 6) and **1.8× the tool calls** (13–14 vs 7–8).
  Two causes, both visible in the traces:
  - The `api` engine's model batches several tool calls per turn; the `agent-sdk` model made one per turn.
  - With the README cut off, the `agent-sdk` agent searched around it: README headings, a `grep` for Pulse and
    RB2B, extra `list_tree`s, and a Tier 3 read.

  Errors are the same in both: no duplicate calls, no schema rejections, no permission denials. The lockdown held,
  and no built-in tool was ever attempted. Every run's first `write_overview` was rejected for going over the word
  limit, then accepted on the second try (2 write attempts everywhere). That is the same prompt issue
  `conversation-caching.md` noted, and it is independent of the engine.
- **Quality:** `api` is better: judge total 17 [16–18] vs 14 [12–16] on E2B, and 16 vs 14 locally. The gap is in
  accuracy and coverage. What-not-how and clarity tie. Spot check (`e2b/api/run-3`, 18, vs
  `e2b/agent-sdk/run-3`, 12): I agree with the judge. The `agent-sdk` overview rates Client Pulse, RB2B and the
  digests "Low, known only from migration and route names", while the `api` overview describes them from the
  README. The judge's reasons point at exactly the README content that the preview cut off.
- **Environment:** local and E2B runs of the same engine behave alike: 6 vs 6 turns for `api`, 14 vs 15 for
  `agent-sdk`, and similar token totals. E2B adds about 12 s (`api`) to 14 s (`agent-sdk`) end-to-end for sandbox
  start, upload and download.

**Verdict.** On this repo and out of the box, the `api` engine is better on every question: as cheap or cheaper,
57% faster, about 3 judge points better, and with fewer turns. But the comparison is not yet a fair test of the
Agent SDK as an agent loop, because its harness silently truncated the most important input. That trade is the
lesson: a harness gives you a loop, caching and context management for free, and also policies you didn't choose.

**Next step** (a separate change, about $0.60 to re-measure only the `agent-sdk` half): keep every tool result
under the 50,000-character inline cap, for example by capping Tier 1 reads by characters as well as lines in
`RepoSandbox`. That treats both engines the same. Alternatively, pass a larger `maxResultSizeChars` per tool, but
the CLI caps a finite value at 50,000. Then re-run the bench to see what remains of the gap.
