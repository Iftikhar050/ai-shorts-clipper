# Project instructions (for AI coding assistants)

AI Shorts Clipper turns long videos into vertical short clips on a CPU-only
Windows laptop. Read `README.md` first.

## Map

| Area | Where |
|---|---|
| CLI entry point | `main.py` |
| Pipeline orchestration | `clipping/runner.py` |
| Download, transcription, AI analysis | `clipping/engine.py`, `clipping/providers/` |
| Prompt templates | `clipping/prompts/` (`analysis.en.md`, `analysis.id.md`) |
| Scoring, signals, events, visual review | `clipping/scoring.py`, `clipping/events.py`, `clipping/visual.py` |
| Clip controls, post-processing | `clipping/selection.py`, `clipping/postprocess.py` |
| Rendering | `clipping/studio/` |
| Web app | `app/` (`server.py`, `jobs.py`, `static/`), started by `run_app.py` |
| Performance tracking | `clipping/performance.py`, `run_performance.py` |
| Docs | `docs/` |

## Rules

- Target environment: Windows, Python 3.12 in `.venv`, CPU only. Install with
  `requirements-cpu.txt`; don't add GPU-only or AGPL dependencies to it.
- The web app runs the CLI (`main.py`) in a subprocess; add pipeline features
  as CLI flags first, then expose them in `app/jobs.py` and the UI.
- `tests/fixtures/prompt_id_*.txt` freeze the Indonesian prompt byte-for-byte;
  new prompt sections must render empty unless enabled.
- Never echo API keys back to the browser or logs; never commit `.env`,
  `accounts.json`, cookies, `app/data/`, `outputs/` or `performance.sqlite3`.
- Add or update tests in `tests/` for every behaviour change and run
  `.\.venv\Scripts\python.exe -m pytest tests` before committing.
- Update the matching doc in `docs/` when behaviour changes.
