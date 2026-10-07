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
