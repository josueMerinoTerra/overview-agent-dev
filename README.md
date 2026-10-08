# Product Overview Agent

Writes a `PROJECT_OVERVIEW.md` (what the product does, not how) for a repository.
Instructions live in `prompts/overview_agent.md`; `tools.py` enforces its hard rules in code (ignore lists, Tier 3
budget, single writable file).

You can run it two ways. Both run the **same agent** (`agent.py` + `tools.py`) with the same prompt and limits;
only *where* it runs changes.

| | Local run (`agent.py`) | Remote run (`remote.py`) |
|---|---|---|
| Where the agent runs | Your machine | An [E2B](https://e2b.dev) cloud sandbox, deleted after the run |
| Target | A local folder | A local folder (uploaded) or a public git URL (cloned in the sandbox) |
| Where the overview goes | **Into the target folder** (`<repo>/PROJECT_OVERVIEW.md`) | `overviews/<repo-name>/` in this project. The target is never modified |
| Keys needed | `ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` + `E2B_API_KEY` |
| One-time setup | venv | venv + build the E2B template |
| Measured time (small repo) | ~15 s | ~25 s (≈10 s of sandbox start, upload/clone, download) |
| Measured cost (small repo) | ~$0.03 Anthropic | ~$0.03 Anthropic + a few seconds of E2B sandbox time |

Measurements: `specs/e2b-remote-run.md` (Results).

## What you need

- **Python 3.9+** and git.
- **An Anthropic API key** (https://console.anthropic.com), for both modes.
- **For remote runs:** an **E2B account and API key** (https://e2b.dev/dashboard; the free Hobby tier is enough,
  its sandboxes live up to 1 hour and a run needs well under that), and internet access.

## Setup (once)

```bash
cd overview-agent-dev
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env        # then fill in the keys; every variable is explained in .env.example
```

Configuration, all in `.env` (or real environment variables, which win; CLI flags win over both):

| Variable | Used by | Required | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | both | yes (local: or `ANTHROPIC_AUTH_TOKEN`) | Calls Claude |
| `E2B_API_KEY` | remote | yes for remote | Creates sandboxes and builds the template |
| `OVERVIEW_MODEL` | both | no (default `claude-sonnet-5-5`) | Model, or `--model` |
| `OVERVIEW_MAX_TURNS` | both | no (default 25) | Turn limit, or `--max-turns` |
| `OVERVIEW_MAX_TOKENS` | both | no (default 16000) | Output tokens per turn, or `--max-tokens` |
| `OVERVIEW_REPO_PATH` | local | no (default `.`) | Repo used when `agent.py` gets no path |

## Run locally

```bash
python agent.py ../dayNight                        # or any repo path; default is $OVERVIEW_REPO_PATH
python agent.py ../dayNight --model claude-opus-5-5
python agent.py ../dayNight --metrics-json run.json   # also save the run metrics
```

What happens: the agent explores the folder through the sandboxed tools, then writes
`../dayNight/PROJECT_OVERVIEW.md`. The trace goes to stderr; the summary (with the real Tier 3 file count and run
metrics) goes to stdout.

Implications:
- **It writes into the target folder** (only `PROJECT_OVERVIEW.md`; nothing else can be written). Run it on a copy
  if you don't want that file in the repo.
- **What the agent reads is sent to Anthropic** as part of the conversation (that's how the model sees it). Files
  matching the ignore lists are never read (see "Secrets and ignored files").

## Run remotely (E2B)

```bash
python e2b_template.py                             # once, and again only if the agent's dependencies change
python remote.py ../dayNight                       # local folder: uploaded, minus ignored files
python remote.py https://github.com/org/repo       # public git repo: cloned inside the sandbox
python remote.py github.com/org/repo               # same; git@github.com:org/repo.git also works (cloned over https)
python remote.py ../dayNight --out /tmp/result     # results somewhere else
python remote.py ../dayNight --keep                # leave the sandbox running to inspect it (see below)
```

What happens, per run:
1. Checks both keys, then clears the previous results in `overviews/<repo-name>/`.
2. Starts a sandbox from the `overview-agent` template (Python, git, the `anthropic` SDK; no secrets).
3. Uploads `agent.py`, `tools.py` and the prompt, so the sandbox always runs your current code.
4. Gets the repo: a local folder is packed into one compressed archive (a "tarball") in memory, uploaded and
   unpacked; a git URL is cloned there (`--depth 1`).
5. Runs `python agent.py` in the sandbox. Your `ANTHROPIC_API_KEY` is passed to that one command only. The trace
   streams live to your terminal.
6. Downloads `PROJECT_OVERVIEW.md` (only if the agent succeeded) and `metrics.json` into `overviews/<repo-name>/`.
7. Kills the sandbox, also on errors and Ctrl-C.

Implications:
- **Your files leave your machine twice:** a local folder is uploaded to E2B's servers, and, as in a local run, what
  the agent reads goes to Anthropic. Ignored files (secrets, `node_modules`, …) are never uploaded.
- **Your Anthropic key enters the sandbox** for the agent command only; it is never in the template or any image.
  The agent has no shell or network tool, so a malicious repo can't make it leak the key.
- **Git URLs must be public.** The sandbox has no credentials, so private repos fail fast with git's message. For
  a private repo, clone it locally and pass the folder instead.
- **Costs:** Anthropic tokens as in a local run, plus E2B sandbox time (seconds per run). Limits: the sandbox lives
  at most 15 minutes and the agent command 14 minutes.
- **`--keep` keeps billing** until the sandbox's 15-minute timeout (or until you kill it in the E2B dashboard).
- Same-named repos share `overviews/<name>/`; a new run replaces the old results. Use `--out` to keep both.

## Secrets and ignored files

Both modes use the same lists in `tools.py`: `IGNORED_DIRS` (e.g. `node_modules`, `.git`, `vendor`, `Pods`,
`.terraform`) and `IGNORED_FILE_PATTERNS` (lockfiles, generated code, and secrets such as `.env*`, `*.pem`, `*.key`,
`*.p12`, `id_rsa*`, `.npmrc`, `.envrc`, `.git-credentials`, `*.tfstate`, `credentials.json`). Matching files are
never read by the agent and never uploaded.

Generic names like `secrets/`, `target/` or `out/` are **not** ignored, because in some repos they are product code.
If a project keeps secrets under another name, add the pattern to `tools.py` before running on it.

## Troubleshooting (remote)

| Message | Fix |
|---|---|
| `error: E2B_API_KEY not set` / `ANTHROPIC_API_KEY not set` | Add it to `.env` |
| `error: E2B rejected the API key` | Check `E2B_API_KEY` in the E2B dashboard |
| `error: could not start a sandbox … (did you run python e2b_template.py?)` | Build the template once |
| `error: git clone failed: …` | Typo, or a private repo: use a local folder instead |
| `error: not a git URL or an existing directory` | Check the path or URL |
| `error: sandbox timed out` | The run exceeded 14-15 minutes; try a lower `--max-turns` |

## Tests

`python3 -m unittest -v`. Offline: no API keys, no network (E2B is replaced by a fake sandbox).
