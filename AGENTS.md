# Repository Guidelines

## Architecture

- `canvasapi/`: configuration-free Canvas HTTP and resource library. It must not read the environment, current directory, or application config.
- `canvascli/`: interactive application configuration, formatting, downloads, and the existing command surface.
- `canvascontext/`: batch course mirror, Markdown rendering, manifests, status generation, and transcripts.
- `canvas.py` and `canvascontext.py`: compatibility/convenience entry points only.
- Legacy standalone scripts remain while callers migrate.

Dependency direction is `canvascli -> canvasapi <- canvascontext`. Do not import either child application from the other.

## Development

- Create a virtual environment with `python3 -m venv .venv` and install with `pip install -e '.[test]'`.
- Run tests with `python3 -m pytest -q`.
- Run the legacy CLI from a directory containing `canvas_config.json`.
- Copy `canvascontext.example.toml` to an ignored local `canvascontext.toml` before running `python3 canvascontext.py sync`.

## Style and testing

- Python 3.10+; use type hints, small functions, and import-safe modules.
- Inject HTTP sessions into `CanvasClient` for unit tests.
- Follow Canvas pagination links as opaque URLs.
- Test permission failures, pagination, incremental writes, and status classification.
- Live Canvas access is a smoke test, not part of the normal test suite.

## Data and sync behavior

- Canvas synchronization is read-only.
- Preserve raw stable JSON and render separate human-readable Markdown.
- Write files atomically and avoid rewriting unchanged source content.
- Record inaccessible resources as warnings; do not abort other courses.
- Never delete mirrored resources immediately when they disappear remotely; mark them stale in the manifest.
- Do not modify or remove user-provided transcripts during synchronization.
