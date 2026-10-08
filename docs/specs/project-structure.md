# Spec: organize the project into a package with one entry point

Status: implemented · Baseline: `233e296` · Executor: the `refactor` sub-agent
(`.claude/agents/refactor.md`) · Plan: `docs/plans/2026-10-08-project-structure.md`

## Context
Everything sits flat at the root: four modules, three test files, and design docs split between `specs/` and
`docs/superpowers/plans/`. Three problems follow from that:

- `tools.py` (437 lines) has three jobs: ignore/secret rules, the `RepoSandbox` jail with its Tier budgets and
  overview validation, and the `TOOLS` schemas the model sees.
- `remote.py` and `e2b_template.py` import `agent.py`, a CLI script, only to reach `load_dotenv`, `trace` and
  `DEFAULT_MODEL`.
- There are three entry scripts (`agent.py`, `remote.py`, `e2b_template.py`), each with its own `argparse`, and
  the E2B upload depends on a hand-kept file list (`AGENT_FILES`).

This is a **pure reorganization**. The goal is high cohesion (one job per module), dependencies that point one
way, and a single front door. Nothing the agent does or costs changes.

## Change

### 1. Layout
```
main.py                     single entry point: subcommands local | remote | build-template
overview_agent/
  __init__.py               one-line docstring only, no imports
  config.py                 PROJECT_ROOT, DEFAULT_MODEL, load_dotenv(), trace()
  overview_format.py        OVERVIEW_NAME, REQUIRED_HEADINGS, MAX_WORDS, SOFT_WORDS, validate_overview()
  ignore_rules.py           IGNORED_DIRS, LOCKFILES, IGNORED_FILE_PATTERNS, is_ignored_dir(), is_ignored_name()
  sandbox.py                ToolError, RepoSandbox, Tier 1/3 limits, scan limits, MANIFESTS, DOCS_INDEX_STEMS
  tool_schemas.py           TOOLS
  agent.py                  load_instructions(), run(): API loop, caching, metrics
  remote.py                 TEMPLATE, parse_source, repo_name, output_dir, make_tarball, run_remote
  e2b_template.py           template(), build()
  prompts/overview_agent.md
tests/
  __init__.py
  test_agent.py  test_sandbox.py (was test_tools.py)  test_remote.py
  test_main.py  test_config.py  test_overview_format.py   (new)
docs/
  specs/    conversation-caching.md, e2b-remote-run.md, prompt-separation.md, project-structure.md
  plans/    2026-10-07-e2b-remote-run.md (+ this refactor's plan)
```
Removed from the root: `agent.py`, `tools.py`, `remote.py`, `e2b_template.py`, `test_*.py`, `prompts/`,
`specs/`, `docs/superpowers/`. Unchanged at the root: `.env`, `.env.example`, `requirements.txt`, `README.md`,
`CLAUDE.md`, `.gitignore`, and `overviews/` (remote run results).

Use `git mv` for every move so history follows the files.

### 2. Dependency direction
```
main.py
  -> overview_agent.agent, .remote*, .e2b_template*        (* imported inside the subcommand handler)
       -> sandbox, tool_schemas
            -> ignore_rules -> overview_format
            -> config
```
- No module imports `agent` except `main.py`. `remote` and `e2b_template` import `config`, not `agent`.
- `e2b_template` imports `TEMPLATE` from `remote`, as it does today.
- `main.py` imports `remote` and `e2b_template` **lazily**, inside their handlers. The E2B sandbox image has only
  Python, git and `anthropic`, not `e2b`, so `python main.py local` must never import `e2b`.

### 3. What moves where
| From | To |
|---|---|
| `agent.py`: `DEFAULT_MODEL`, `HERE`, `load_dotenv`, `trace` | `config.py` (`HERE` becomes `PROJECT_ROOT`, the repo root, so `.env` is still read from the root) |
| `agent.py`: `main()` (argparse) | `main.py` `local` subcommand |
| `agent.py`: `load_instructions` | stays; the prompt path becomes `Path(__file__).parent / "prompts" / "overview_agent.md"` |
| `tools.py`: `OVERVIEW_NAME`, `REQUIRED_HEADINGS`, `MAX_WORDS`, `SOFT_WORDS`, `_validate_overview`, `_sections` | `overview_format.py`: `validate_overview(content, cited_path_problem) -> (errors, warnings)` becomes a plain function. The evidence-path check needs the sandbox's path rules, so the sandbox passes it in as a callback: `cited_path_problem(token) -> warning or None` |
| `tools.py`: ignore sets, patterns, `is_ignored_*` | `ignore_rules.py` |
| `tools.py`: `ToolError`, `RepoSandbox`, `_stderr`, Tier/scan constants, `MANIFESTS`, `DOCS_INDEX_STEMS` | `sandbox.py`; `write_overview` calls `validate_overview(content, self._cited_path_problem)`, where `_cited_path_problem` holds the old `try/_resolve/except ToolError` lines verbatim |
| `tools.py`: `TOOLS` | `tool_schemas.py` |
| `remote.py`: `main()` (argparse) | `main.py` `remote` subcommand |
| `remote.py`: `HERE / "overviews"` | `config.PROJECT_ROOT / "overviews"` |
| `e2b_template.py`: `main()` | `build()` stays in the module; `main.py build-template` calls it |

Function bodies, messages, constants and the `TOOLS` schemas are moved verbatim. The only edits are imports, the
paths listed above, the `validate_overview` callback, and one message: the sandbox-start error hint
`did you run python e2b_template.py?` becomes `did you run python main.py build-template?`.

### 4. `main.py` CLI
`load_dotenv()` runs before parsing, because the defaults read the environment. Shared options
(`--model`, `--max-turns`, `--max-tokens`) come from one parent parser, with the same env defaults as today:
`OVERVIEW_MODEL` / `DEFAULT_MODEL`, `OVERVIEW_MAX_TURNS` / 25, `OVERVIEW_MAX_TOKENS` / 16000.

| Command | Options | Behavior (same as the old script) |
|---|---|---|
| `python main.py local [repo]` | shared + `--metrics-json` | `repo` defaults to `$OVERVIEW_REPO_PATH` or `.`; prints the API-key note if no key; exits with `run(...)`'s code |
| `python main.py remote <source>` | shared + `--out`, `--keep` | bad source: one `error:` line, exit 1, no sandbox; Ctrl-C: `interrupted`, exit 130; else `run_remote(...)`'s code |
| `python main.py build-template` | none | builds the E2B template |

A subcommand is required. With none, argparse prints usage and exits 2. `main(argv=None) -> int` takes `argv` for
tests, and `if __name__ == "__main__": sys.exit(main())`.

### 5. E2B upload
- `AGENT_FILES` (a hand-kept tuple) becomes `agent_files(root=PROJECT_ROOT)`. It returns `main.py` plus every
  `.py` and `.md` file under `overview_agent/`, sorted, as POSIX paths relative to `root`. A new module can't be
  forgotten, and `__pycache__/*.pyc` and `.DS_Store` are never uploaded (they aren't UTF-8 text, and uploading
  reads every file as text).
- The sandbox command becomes `python main.py local <REPO_DIR> --model … --max-turns … --max-tokens …
  --metrics-json …` with `cwd=AGENT_DIR`. The API key handling, timeouts and downloads are unchanged.
- The template does not change, so no rebuild is needed.

### 6. Docs
- `README.md`: every command uses `python main.py …`; "uploads `agent.py`, `tools.py` and the prompt" becomes
  "uploads `main.py` and the `overview_agent/` package"; `specs/` links become `docs/specs/`.
- `CLAUDE.md`: Commands, the "Where responsibilities live" list (the new module names), the `prompts/` path,
  `specs/` becomes `docs/specs/`, plans live in `docs/plans/`, and the sync rule points at `overview_format.py`.
- `.env.example`: the three section comments that name `agent.py`, `remote.py` or `e2b_template.py` name the
  `main.py` subcommands instead.
- Cross-links between specs and plans are updated to the new paths. Historical descriptions inside the old specs
  stay as written (they record what was true then).
- `.claude/agents/refactor.md` and `.gitignore` are unchanged.

### Out of scope
Agent behavior, prompt text, tool schemas, prompt caching, metrics, the E2B template, packaging
(`pyproject.toml` / `pip install -e .`), and new features.

## Tests
- The 48 existing tests move to `tests/`, with only imports, mock targets and paths changed:
  - `import agent` becomes `from overview_agent import agent`.
  - `test_tools.py` becomes `test_sandbox.py`, importing from `sandbox`, `ignore_rules` and `overview_format`.
  - In `test_remote.py`, `remote.HERE` becomes `config.PROJECT_ROOT`, `AGENT_FILES` becomes `agent_files()`, and
    `"python agent.py"` becomes `"python main.py local"`.
  - `MainTests` (the 2 remote CLI tests) moves to `test_main.py` and targets `main.main(["remote", …])`.
- New in `test_main.py`:
  - `local` passes its arguments and env defaults through to `agent.run` (mocked), and prints the API-key note
    when no key is set.
  - `remote`: Ctrl-C gives `interrupted` and exit 130. `build-template` calls `e2b_template.build()`.
  - With no subcommand, the exit code is 2.
  - In a subprocess with `e2b` blocked (`sys.modules["e2b"] = None`), `import main` succeeds and
    `main.py local --help` exits 0. This proves the sandbox can run without `e2b`.
  - `python <root>/main.py local --help`, run from another working directory, exits 0.
- New in `test_remote.py`: `agent_files()` includes `main.py`, `overview_agent/agent.py`,
  `overview_agent/sandbox.py` and the prompt. On a temporary tree, it skips `.DS_Store` and `__pycache__/*.pyc`.
  The sandbox command starts with `python main.py local /home/user/repo`.
- New `test_config.py`: `PROJECT_ROOT` is the repo root, and `load_dotenv` reads `PROJECT_ROOT/.env` by default.
- New `test_overview_format.py`: `validate_overview` turns callback results into warnings, and doesn't call the
  callback when the headings are wrong.
- `python3 -m unittest -v` from the root runs everything, with no new arguments needed (`tests/__init__.py` makes
  it discoverable on Python 3.9).
- Manual check: `python main.py --help`, `local --help` and `remote --help` show the same options and defaults as
  the old scripts.
- Optional live check (needs `ANTHROPIC_API_KEY`): `python main.py local <small repo>` writes an overview, as
  before.

## Results (2026-10-08)
- Tests: 48 before (`233e296`), 62 after, all passing offline (`python3 -m unittest -v`). The 14 new tests cover
  config paths, the CLI subcommands, running without `e2b`, the upload list, and `validate_overview`.
- CLI: `main.py local --help` and `main.py remote --help` list the same options and defaults as the old
  `agent.py` / `remote.py` scripts.
- Live runs: not repeated. The agent code, prompt and tool schemas are unchanged (the schemas are byte-identical).
