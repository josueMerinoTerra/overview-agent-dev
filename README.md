# Product Overview Agent

Writes a `PROJECT_OVERVIEW.md` (what the product does, not how) at the root of a local repo.
Instructions live in `prompts/overview_agent.md`; `tools.py` enforces its hard rules in code (ignore list, Tier 3 budget, single writable file).

```bash
cd overview-agent-dev
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # then put your key in .env (ANTHROPIC_API_KEY=...); model, limits and default repo are there too
python agent.py ../dayNight            # or any repo path; default is the current directory
python agent.py ../dayNight --model claude-opus-5-5
```

Trace goes to stderr; the final summary (including the real Tier 3 file count) goes to stdout.
Tests (no API key needed): `python3 -m unittest -v`

## Remote run (E2B)
Runs the same agent inside an [E2B](https://e2b.dev) sandbox. The target repo is cloned there (git URL) or uploaded
(local folder, minus ignored folders and secret files), and the results land in `overviews/<repo-name>/`. Your
local folder is never modified.

```bash
# once: put E2B_API_KEY in .env, then build the sandbox template
python e2b_template.py
python remote.py https://github.com/org/repo      # or: github.com/org/repo
python remote.py ../dayNight                       # local folder
python remote.py ../dayNight --keep                # leave the sandbox running to inspect it
```

Files that are never uploaded or read are listed in `IGNORED_DIRS` and `IGNORED_FILE_PATTERNS` in `tools.py`. If a
project keeps secrets under another name, add the pattern there before running on it.
