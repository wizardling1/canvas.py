# Canvas tools

This project has three independent layers:

- `canvasapi`: a configuration-free Python library for the Canvas REST API.
- `canvascli`: the original interactive CLI (`status`, `pick`, `ls`, and `fetch`).
- `canvascontext`: an incremental, agent-readable mirror of selected courses.

`canvascli` and `canvascontext` both depend on `canvasapi`; they do not depend on each other.

## Development environment

Create a virtual environment and install the project in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
```

Run tests with:

```bash
python3 -m pytest -q
```

## canvasapi

The library never reads configuration files or environment variables:

```python
from canvasapi import CanvasClient
from canvasapi.courses import get_course
from canvasapi.modules import list_modules

client = CanvasClient(
    base_url="https://canvas.example.edu",
    token="...",
)
course = get_course(client, 123456)
modules = list_modules(client, 123456)
```

Resource modules currently cover courses, modules/items, assignments, submissions, pages, files, announcements, discussions, and calendar events.

## canvascli

The legacy `canvas.py` entry point is retained. It reads `canvas_config.json` from the current directory, with `CANVAS_TOKEN` taking precedence over the file token.

```bash
python3 canvas.py --help
python3 canvas.py status
python3 canvas.py pick
python3 canvas.py pick --set-id 123456
python3 canvas.py ls
python3 canvas.py fetch ./downloads
```

Commands and nested commands provide contextual help. Argument errors print the
relevant command help before the error message:

```bash
python3 canvas.py fetch --help
python3 canvascontext.py transcript --help
python3 canvascontext.py transcript add --help
```

The older standalone scripts remain available during the migration. Each
supports `--help`; tools that read configuration also accept `--config`, and
the legacy download tools accept `--output`.

## canvascontext

Copy `canvascontext.example.toml` to a local `canvascontext.toml`, then set your Canvas base URL, output directory, and course IDs. Local configuration and mirrored course content are ignored by Git.

Synchronize all three:

```bash
python3 canvascontext.py sync
```

Or synchronize one configured course by slug:

```bash
python3 canvascontext.py sync example-course
```

The default output is `courses/`, which is ignored by Git. Each course contains raw JSON, rendered Markdown, downloaded files, a SQLite manifest, `INDEX.md`, and deterministic `STATUS.md`.

Useful offline commands:

```bash
python3 canvascontext.py verify
python3 canvascontext.py status
python3 canvascontext.py transcript add example-course \
  --date 2026-08-25 < transcript.txt
```

Every command level provides its own help and examples:

```bash
python3 canvascontext.py --help
python3 canvascontext.py transcript --help
python3 canvascontext.py transcript add --help
```

`status`, `verify`, and transcript ingestion do not require a working Canvas token. Synchronization uses `CANVAS_TOKEN` or the `token_file` configured in `canvascontext.toml`.

Canvas may deny the course-wide Files or Pages indexes to students. `canvascontext` records those warnings and still discovers accessible files and pages through module items, assignments, and page links.

## Tests

```bash
python3 -m pytest -q
```

Live checks use the local token and are deliberately separate from the mocked test suite.
