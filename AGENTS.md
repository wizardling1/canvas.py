# Repository Guidelines

## Project Structure & Module Organization
- Root CLI: `canvas.py` (entry), plus `pick_class.py`, `list_pdfs.py`, `list_assignments.py`, `fetch_pdfs.py`, `fetch_assignments.py`, `zoom.py`.
- Shared package: `canvascli/` with `api.py` (auth, `iter_paginated`), `config.py` (config loader), `utils.py` (links, names), `formatting.py` (sizes, dates).
- Config: `canvas_config.json` in the current working directory `{ "token": "…", "course_id": 123 }`.
- Downloads: `./downloads/` or auto-named `<course>_downloads/`.
- Tests: `tests/` (e.g., `tests/test_list_assignments.py`).

## Build, Test, and Development Commands
- Create venv: `python3 -m venv .venv && source .venv/bin/activate`.
- Install deps: `pip install -U requests tabulate playwright browser_cookie3`.
- Playwright (for `zoom.py`): `python -m playwright install`.
- Status: `python canvas.py`.
- Pick course: `python canvas.py pick --search math`.
- List: `python canvas.py ls`.
- Fetch: `python canvas.py fetch ./my_course_downloads`.
- Standalone: `python list_assignments.py --format table`, `python list_pdfs.py`, `python fetch_pdfs.py`.
- Tests: `pytest -q`.

## Coding Style & Naming Conventions
- Python 3.10+; PEP 8; 4-space indentation; prefer type hints and small functions.
- Names: `snake_case`; modules are task-oriented verbs (`fetch_*`, `list_*`).
- HTTP: reuse one `requests.Session`, set timeouts, and paginate via `canvascli.api.iter_paginated`.

## Testing Guidelines
- Framework: `pytest`; stub HTTP with `responses` or `requests-mock`.
- Tests under `tests/`, named `test_*.py`; cover pagination, filtering, and error handling.
- Target ≥80% coverage for changed code. Run with `pytest -q`.
- Never embed real tokens or commit configs.

## Commit & Pull Request Guidelines
- Commits: imperative subject (≤50 chars), concise body explaining rationale/side effects. Example: `Refactor to shared iter_paginated`.
- Link issues (e.g., `Fixes #123`) and group related changes.
- PRs: include summary, repro steps, before/after output, and config notes.

## Security & Configuration Tips
- Do not commit `canvas_config.json` or tokens; keep them local/ignored.
- Prefer `CANVAS_TOKEN` for local runs; file config is a fallback.
- Respect API rate limits; polite delays are already built into pagination.
