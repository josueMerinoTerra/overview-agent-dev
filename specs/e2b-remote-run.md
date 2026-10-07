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
  `.env`, `.env.*`, `*.pem`, `*.key`, plus an existing `PROJECT_OVERVIEW.md`), plus any `OVERVIEW_EXTRA_IGNORE`
  patterns (see below). Symlinks are stored as links and never followed, so a symlink can't pull in a file from
  outside the folder.
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
   command only), plus `OVERVIEW_EXTRA_IGNORE` when it is set. Stream stderr (the trace) live to local stderr, and collect stdout (the summary). Set the
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

### Extra ignore patterns: `OVERVIEW_EXTRA_IGNORE`
The built-in secret patterns can't cover every project's naming (`credentials.json`, `*.p12`, a `secrets/`
folder…). `OVERVIEW_EXTRA_IGNORE` is a comma-separated list of glob patterns, for example
`OVERVIEW_EXTRA_IGNORE=credentials.json,*.p12,secrets`. It is set in `.env` or the environment like the other
`OVERVIEW_*` keys.
- **Add-only.** Patterns are appended to the built-in lists and can never remove or override them, so a typo can't
  expose `.env`.
- Each pattern is matched with `fnmatch` against **every path component**, both file names and directory names.
  A matching directory is skipped together with everything under it.
- **One list for both sides:** it applies to the tarball upload *and* to the agent's own read rules
  (`RepoSandbox`), so it also protects local `agent.py` runs. Blank entries and surrounding whitespace are
  ignored.
- It is read once per use (when `RepoSandbox` is created, and once at the start of `make_tarball`), not at import
  time, so values loaded from `.env` by `load_dotenv()` are seen.
- For a git source, the sandbox's `agent.py` gets the same value through the agent command's `envs`, so the agent
  can't read those files in a cloned repo either.

### Small edits
- `tools.py`: move the ignore rules out of `RepoSandbox` into public module functions:
  `ignore_patterns() -> (dir_names, name_patterns)` (built-ins plus `OVERVIEW_EXTRA_IGNORE`),
  `is_ignored_dir(name, patterns)` and `is_ignored_name(name, patterns)`. `RepoSandbox` and `make_tarball` then
  share one source of truth. With the variable unset, behavior is unchanged, and `test_tools.py` still covers it.
- `requirements.txt`: add `e2b`, pinned to the version the implementation is tested against (the E2B Python API
  has changed between major versions).
- `.env.example`: add `E2B_API_KEY=` and `OVERVIEW_EXTRA_IGNORE=` (with a comment saying it is add-only and
  comma-separated).
- `.gitignore`: add `overviews/`.
- `README.md`: add a "Remote run (E2B)" section (get an E2B key, build the template once, run `remote.py`).
- `CLAUDE.md`: under "Where responsibilities live", add `remote.py` (E2B orchestration) and `e2b_template.py`
  (sandbox image), and add the commands.

### Unchanged
`agent.py` logic, the runtime prompt, and both cache breakpoints. The sandbox runs the same agent as a local run.

### Security notes
- The Anthropic key exists inside the running sandbox only for the agent command. It is never in the template, in
  build logs, or in an image.
- The agent has no shell or network tool, so even a malicious target repo can't make it exfiltrate the key.
- Local uploads never include files that match the built-in secret patterns or `OVERVIEW_EXTRA_IGNORE`. A
  project-specific secret file that matches neither *is* uploaded (the same files a local run would let the agent
  read). Add its pattern to `OVERVIEW_EXTRA_IGNORE` before running on that folder.

## Tests
- New `test_remote.py`, offline with no keys, in the style of `test_agent.py`:
  - `parse_source`: https URL, `git@` URL, `.git` suffix, an existing local dir, a nonexistent path (error)
  - `make_tarball`: includes normal files, excludes `node_modules/`, `.git/`, `.env` and `id.pem`, and does not
    follow a symlink that points outside the folder
  - `output_dir`: `…/repo.git`, a trailing slash, a local path, an explicit `--out`
  - `make_tarball` with `OVERVIEW_EXTRA_IGNORE="credentials.json, secrets"`: excludes `credentials.json` and
    everything under `secrets/`, still excludes `.env`, and includes the other files
  - `run_remote` with a fake sandbox: `ANTHROPIC_API_KEY` appears only in the agent command's `envs`, the
    results land in the out dir, the exit code is passed through, `kill()` is called on success and on failure
    (and not with `--keep`), a missing key exits without calling the factory, and `OVERVIEW_EXTRA_IGNORE` is
    forwarded in `envs` when set
- `test_tools.py`: the existing tests pass unchanged. New tests check that `ignore_patterns()` with
  `OVERVIEW_EXTRA_IGNORE` set blocks `read_file` on a matching file and hides a matching directory from the
  listing. With the variable unset, the built-ins behave as before. An entry like `,, ` adds nothing.
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
