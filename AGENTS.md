# Repository Guidelines

## Project Structure & Module Organization
- Root scripts: `canvas.py` (CLI entry), `pick_class.py`, `list_pdfs.py`, `list_assignments.py`, `fetch_pdfs.py`, `fetch_assignments.py`, `zoom.py`.
- Config: `canvas_config.json` is read from the current working directory (for most commands) and should contain `{ "token": "…", "course_id": 123 }`.
- Downloads: saved under `./downloads/` or an auto-named `<course>_downloads/` directory.

## Setup, Run, and Dev Commands
- Create venv: `python3 -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -U requests tabulate playwright browser_cookie3`
- Playwright browsers (for `zoom.py`): `python -m playwright install`
- Quick status: `python canvas.py`
- Pick course: `python canvas.py pick --search math`
- List: `python canvas.py ls`
- Fetch files: `python canvas.py fetch ./my_course_downloads`
- Standalone: `python list_assignments.py --format table`, `python list_pdfs.py`, `python fetch_pdfs.py`.

## Coding Style & Naming Conventions
- Python 3.10+; PEP 8; 4-space indentation; prefer type hints and small, focused functions.
- Filenames and functions: `snake_case`; modules are task-oriented verbs (e.g., `fetch_*.py`, `list_*.py`).
- HTTP: reuse a `requests.Session` and respect timeouts; avoid global state where possible.

## Testing Guidelines
- Framework: `pytest` (add as needed). Place tests under `tests/`, e.g., `tests/test_list_assignments.py`.
- Use `requests-mock` or `responses` to avoid live Canvas calls; never embed real tokens.
- Name tests `test_*` and cover pagination, filtering, and error handling. Target ≥80% for changed code.
- Run: `pytest -q` (add `pytest` to dev requirements).

## Commit & Pull Request Guidelines
- Commits: imperative mood, concise subject (≤50 chars), body for rationale and side effects.
- Reference issues with `Fixes #123` when relevant. Group related changes.
- PRs: include summary, reproduction steps, before/after output (tables or logs), and any config notes.

## Security & Configuration Tips
- Never commit `canvas_config.json` or tokens. Add to `.gitignore` locally.
- Prefer environment variables for local experiments: e.g., read `CANVAS_TOKEN` when present.
- Be mindful of API rate limits; keep polite delays (already present) and efficient pagination.

