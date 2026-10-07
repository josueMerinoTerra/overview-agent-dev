# Spec: run the agent remotely in an E2B sandbox

Status: designed, not implemented · Baseline: `622c0fd` · Step 1 of 2 (step 2, porting to the Claude Agent SDK,
gets its own spec)

## Context
`agent.py` runs only on the local machine: `RepoSandbox` is a folder jail on the local disk, and the overview is
written into that folder. The goal of this step is **learning deployment**. A working demo is enough. It covers E2B
sandboxes, templates, files, commands and env vars, with the agent itself unchanged.

Decisions made while brainstorming:
- **E2B** (e2b.dev), not AWS EC2.
- **Two steps.** First run today's Anthropic-SDK agent in E2B unchanged (this spec), then port it to the
  Agent SDK (a later spec).
- **CLI, no UI.** The output is a markdown file plus a trace, which a terminal shows well.
- **Input:** a Git URL *or* a local folder.
- **Approach A: ship the whole agent into the sandbox.** The template holds the environment, and the code is
  uploaded on each run. Rejected alternatives: B, a local loop with remote tools (rewrites `tools.py`, adds a
  network round-trip per tool call, breaks the sandbox tests), and C, baking the code into the template (needs a
  rebuild for every prompt edit).
- **Results** go to `overviews/<repo-name>/` in this project (git-ignored). The local folder is never modified.
- **Secrets and library folders:** expand the built-in ignore lists in `tools.py` with always-safe patterns. No
  env-var override (considered as `OVERVIEW_EXTRA_IGNORE`, dropped as YAGNI for a single user).

## Change

### New: `e2b_template.py`
Defines and builds the E2B template `overview-agent` with the Python template builder (`e2b.Template`): the base
image plus `git`, `python3` and `pip install anthropic`. You run it once (`python e2b_template.py`), and again
only when dependencies change. **No secrets in the template:** no API key is set at build time.

### New: `remote.py`
`python remote.py <git-url | local-path> [--model M] [--max-turns N] [--max-tokens N] [--out DIR] [--keep]`

`--model`, `--max-turns` and `--max-tokens` use the same `.env` keys and defaults as `agent.py` and are forwarded
to it unchanged. `.env` is loaded with `agent.load_dotenv` (reused, not copied).

Pure functions (offline-testable):
- `parse_source(arg) -> ("git", url) | ("local", Path)`. Values starting with `https://`, `http://` or `git@`, or
  ending in `.git`, are git. Anything else must be an existing directory, or the run exits with an error.
- `make_tarball(path) -> bytes`: a `.tar.gz` of the folder that **excludes exactly what the agent can't read**:
  ignored dirs (`IGNORED_DIRS` and `.git*`) and ignored names (lockfiles, generated files, and secrets such as
  `.env`, `.env.*`, `*.pem`, `*.key`, plus an existing `PROJECT_OVERVIEW.md`), including the expanded built-ins
  below. Symlinks are stored as links and never followed, so a symlink can't pull in a file from outside the
  folder.
- `output_dir(source, out) -> Path`: `out` if given, else `<project>/overviews/<name>/`, resolved against this
  project's folder, not the cwd. `<name>` is the last path segment with `.git` and trailing slashes stripped.

`run_remote(source, args, sandbox_factory=Sandbox.create) -> int` does the I/O. The factory is a parameter so
tests can inject a fake. Flow:
1. Fail before creating a sandbox if `E2B_API_KEY` or `ANTHROPIC_API_KEY` is missing.
2. Create a sandbox from template `overview-agent` with a 15-minute timeout.
3. Upload `agent.py`, `tools.py` and `prompts/overview_agent.md` to `/home/user/agent/`.
4. Get the repo to `/home/user/repo`:
   - git: `git clone --depth 1 <url> /home/user/repo`
   - local: print the tarball size, write it to `/home/user/repo.tar.gz`, then extract it into `/home/user/repo`
5. Run `python agent.py /home/user/repo --model … --max-turns … --max-tokens … --metrics-json
   /home/user/metrics.json` with `cwd=/home/user/agent` and `envs={"ANTHROPIC_API_KEY": …}` (passed to this
   command only). Stream stderr (the trace) live to local stderr, and collect stdout (the summary). Set the
   command's own timeout explicitly to 14 minutes. E2B's per-command default is much shorter than an agent run,
   so it must not be left at the default.
6. Download `/home/user/repo/PROJECT_OVERVIEW.md` (if written) and `/home/user/metrics.json` (if present) into
   `output_dir`.
7. Print the agent's summary, then the local paths of the downloaded files.
8. In `finally`, kill the sandbox. This also covers Ctrl-C. `--keep` skips the kill and prints the sandbox id so
   you can inspect it while debugging.

Exit code: the agent's exit code, or 1 for any failure in `remote.py` itself.

### Errors
Each failure prints one `error: …` line and exits non-zero. The sandbox is killed in every case except `--keep`.

| Failure | Behavior |
|---|---|
| A key is missing | Exit before sandbox creation, so it costs nothing |
| Local path isn't a directory | Exit before sandbox creation |
| Template not found | `error: template overview-agent not built, run python e2b_template.py` |
| `git clone` fails | Show git's stderr, exit 1 |
| Agent exits non-zero | Still download `metrics.json` if present, pass the agent's exit code through |
| Sandbox timeout | `error: sandbox timed out`, exit 1 |

### Expanded built-in ignore lists (`tools.py`)
Uploading a local folder adds a second destination for its files (E2B's servers, on top of the Anthropic API that
already sees whatever the agent reads), so the built-in lists grow. Only patterns that are **never product code in
any repo** qualify:
- `IGNORED_FILE_PATTERNS`, secrets: `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `id_rsa*`, `id_ed25519*`, `*.kdbx`,
  `.npmrc`, `.pypirc`, `.netrc`, `*.tfstate`, `*.tfstate.*`, `*.tfvars`, `credentials.json`,
  `service-account*.json`
- `IGNORED_DIRS`, library and cache folders: `Pods`, `Carthage`, `bower_components`, `.gradle`, `.terraform`,
  `.tox`, `.mypy_cache`, `.pytest_cache`, `.dart_tool`

Generic names that can be real source folders (`secrets`, `target`, `out`, `env`, `deps`) are deliberately **not**
built in: hiding them could hide the core of a product. When a specific project needs one skipped, you add it to
`tools.py` by hand. There's no env-var override (YAGNI while there's a single user). Add one only if the tool gets
shared.

This also changes local `agent.py` runs: the agent can no longer read these files. They're secrets or library
folders, so the overview shouldn't change. The measurement protocol checks this.

### Small edits
- `tools.py`: move the ignore rules out of `RepoSandbox` into public module functions `is_ignored_dir(name)`
  (`IGNORED_DIRS` or a `.git*` prefix) and `is_ignored_name(name)` (lockfiles and `IGNORED_FILE_PATTERNS`), so
  `RepoSandbox` and `make_tarball` share one source of truth. Apart from the expanded lists above, this is a pure
  refactor, and `test_tools.py` still covers it.
- `requirements.txt`: add `e2b`, pinned to the version the implementation is tested against (the E2B Python API
  has changed between major versions).
- `.env.example`: add `E2B_API_KEY=`.
- `.gitignore`: add `overviews/`.
- `README.md`: add a "Remote run (E2B)" section (get an E2B key, build the template once, run `remote.py`).
- `CLAUDE.md`: under "Where responsibilities live", add `remote.py` (E2B orchestration) and `e2b_template.py`
  (sandbox image), and add the commands.

### Unchanged
`agent.py` logic, the runtime prompt, the tool schemas, and both cache breakpoints. The sandbox runs the same agent
as a local run.

### Security notes
- The Anthropic key exists inside the running sandbox only for the agent command. It is never in the template, in
  build logs, or in an image.
- The agent has no shell or network tool, so even a malicious target repo can't make it exfiltrate the key.
- Local uploads never include files that match the built-in ignore lists. A secret file with a project-specific
  name that matches none of them *is* uploaded (the same file a local run would let the agent read). Add its
  pattern to `tools.py` before running on that folder.

## Tests
- New `test_remote.py`, offline with no keys, in the style of `test_agent.py`:
  - `parse_source`: https URL, `git@` URL, `.git` suffix, an existing local dir, a nonexistent path (error)
  - `make_tarball`: includes normal files, excludes `node_modules/`, `.git/`, `.env` and `id.pem`, and does not
    follow a symlink that points outside the folder
  - `output_dir`: `…/repo.git`, a trailing slash, a local path, an explicit `--out`
  - `make_tarball` with new built-ins: excludes `config/credentials.json`, `certs/push.p12`, `infra/x.tfstate`
    and `ios/Pods/`, and still includes a `src/secrets/` source folder (generic names are not built in)
  - `run_remote` with a fake sandbox: `ANTHROPIC_API_KEY` appears only in the agent command's `envs`, the
    results land in the out dir, the exit code is passed through, `kill()` is called on success and on failure
    (and not with `--keep`), and a missing key exits without calling the factory
- `test_tools.py`: the existing tests pass unchanged. New tests check that `read_file` refuses a new built-in
  secret (`credentials.json`, `*.tfstate`), that `list_tree` hides a new built-in folder (`Pods/`), and that
  `is_ignored_dir` / `is_ignored_name` agree with what `RepoSandbox` enforces.
- `test_agent.py`: unchanged and passing.

## Measurement protocol (to do)
- **Runs:** one remote run on a local folder (`../dayNight`) and one on a small public GitHub repo, plus a local
  `agent.py` run on the same `dayNight` copy for comparison. All with the same model and limits.
- **Metrics:** turns, tool calls, tool errors, `first_write_ok`, input, output and cache tokens (from
  `metrics.json`), and wall time. Also record the end-to-end time of `remote.py` (including sandbox start and
  upload) separately from the agent's own `wall_seconds`.
- **Success:** both remote runs write a valid overview. Turns and tokens match the local run within normal run
  variance (same agent, same prompt). The only expected difference is the sandbox and upload overhead on top of
  wall time.
- **Expanded ignore lists:** a local run on `dayNight` at baseline `622c0fd` vs this change gives an overview of
  the same quality, with the same Tier 3 files (or a different file only where the old one is now ignored).
