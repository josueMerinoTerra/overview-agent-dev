# Spec: a second engine on the Claude Agent SDK, benchmarked against the API loop

Status: approved design, not implemented · Baseline: `0c57003` · Step 2 of 2 (step 1:
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
  - `system_prompt=load_instructions()` (the same `prompts/overview_agent.md`; the file form if the string is too
    long for the command line)
  - `model`, `max_turns`, `max_budget_usd=0.50`
  - `tools=[]`, `allowed_tools=["mcp__overview__*"]`, `permission_mode="dontAsk"`
  - `setting_sources=[]`, `strict_mcp_config=True`
  - `env`: `CLAUDE_CONFIG_DIR=<fresh temp dir>`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
    `ENABLE_CLAUDEAI_MCP_SERVERS=false`, and tool search off
  - `cwd=<the same temp dir>`, never the target repo
  
  Tool search is off so both engines see every tool schema from turn 1. The exact switch is confirmed during
  implementation; if there is none, the spec records the difference instead.
- **Flow.** `ClaudeSDKClient`: send the same first user message as the `api` engine, read until the
  `ResultMessage`. If `sandbox.overview_written` is still false, send the same one-time reminder and read again.
- **Metrics.** The same keys as the `api` engine plus `engine`. `turns` and the four token counts are summed over
  the one or two `ResultMessage`s. `wall_seconds` is measured by us, as in `agent.py`. Recorder fields come from
  the shared `ToolRecorder`.
- **Errors.** An `error_*` result subtype, or an exception from the SDK, prints one `error: …` line and exits 1.
  `error_max_turns` and `error_max_budget_usd` name the limit that was hit. Metrics are still written.

Known differences, recorded rather than fixed:
- The prompt says `write_overview`; this engine's model sees `mcp__overview__write_overview`. The prompt is left
  identical for both engines.
- `--max-tokens` doesn't apply to this engine (no such option).
- Caching is the SDK's own, not our two breakpoints.

### New: `overview_agent/recorder.py`, `ToolRecorder`
`agent.py` counts tool calls inline today. That code moves into a small class that both engines use, so behavior
counts are computed by the same code:
- `call(sandbox, name, args) -> (output, is_error)`, which wraps `sandbox.call` and catches errors as `agent.py`
  does today
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
Add `pip_install("claude-agent-sdk")`. Rebuild once with `python main.py build-template`. The sandbox runs as a
normal user, and `dontAsk` doesn't need root.

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
  files. E2B runs use `remote.run_remote` with the local path.
- **Per run:** `bench/<ts>/<env>/<engine>/run-N/` holds `PROJECT_OVERVIEW.md`, `metrics.json`, the trace
  (`trace.log`), `judge.json`, and for E2B the end-to-end seconds.
- **Resume.** A run whose `metrics.json` exists is skipped, so a crash never pays for the same run twice. Pass the
  same `--out bench/<ts>` to resume.
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
- `requirements.txt`: add `claude-agent-sdk`, pinned to the tested version.
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

## Results
Pending.
