# canvas.py

Read-only tools for the [Canvas LMS REST API](https://developerdocs.instructure.com/services/canvas):

- `canvasapi`: configuration-free Python library
- `canvascli`: command-line course browser and local mirror

## Requirements

- Python 3.10+
- Canvas account and site URL, such as `https://canvas.example.edu`
- Canvas access token

## Access token

For personal use or testing:

1. Sign in to Canvas.
2. Open **Account → Settings**.
3. Under **Approved Integrations**, select **New Access Token**.
4. Create and immediately copy the token. Canvas will not show it again.

An institution may disable manual tokens. Multi-user applications must use
OAuth. See the [Canvas authentication documentation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).

Pass the token only through `CANVAS_TOKEN`:

```bash
read -rsp "Canvas token: " CANVAS_TOKEN
printf '\n'
export CANVAS_TOKEN
```

Do not store tokens in source files, `canvasmirror.json`, command arguments, or
the Nix store.

## Install or run

### uv

```bash
uv run canvascli --help
```

Prefix later commands with `uv run`.

### Nix

```bash
nix run .#canvascli -- --help
nix develop
```

With `nix run`, pass command arguments after `--`:

```bash
nix run .#canvascli -- status
```

### pip or another PEP 517 frontend

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Installation creates `canvascli` in the active environment, not `/usr/bin`.

## Create a mirror

```bash
mkdir courses
cd courses
canvascli init --base-url https://canvas.example.edu
canvascli list-courses
canvascli add-course 123456 234567
canvascli sync
canvascli status
```

`init` creates `canvasmirror.json`. Its directory is the mirror root. Commands
run below that directory find it by searching parent directories.

`list-courses` shows active courses and their IDs. Use `--all` to include past,
future, and concluded courses.

Add courses by ID or scope:

```bash
canvascli add-course 123456
canvascli add-course --all-active
canvascli add-course --all
```

Remove courses without deleting their local files:

```bash
canvascli remove-course 123456
```

Synchronize all tracked courses or selected IDs:

```bash
canvascli sync
canvascli sync 123456 234567
```

`CANVAS_TOKEN` is required only for commands that contact Canvas. `init`,
`remove-course`, `status`, and transcript storage are offline.

## Inspect and download

```bash
canvascli ls 123456
canvascli fetch 123456
canvascli fetch 123456 ./downloads
```

`ls` shows visible PDFs and assignments. `fetch` downloads course PDFs and
assignment files outside the mirror workflow.

Add a transcript from an explicit file:

```bash
canvascli transcript add 123456 \
  --date 2026-08-28 \
  --file lecture.txt
```

No `canvascli` command prompts or reads content implicitly from standard input.

## Mirror layout

Each course is stored under its generated slug:

```text
course-slug/
├── .canvas/
│   ├── manifest.sqlite
│   ├── last-sync.json
│   └── raw/
├── files/
├── modules/
├── assignments/
├── pages/
├── announcements/
├── discussions/
├── transcripts/
├── COURSE.md
├── INDEX.md
├── STATUS.md
└── SYLLABUS.md
```

`files/` preserves the visible Canvas folder hierarchy and safe original names.
Files whose folder metadata is inaccessible go under
`_unresolved-folder-ID/`. Files in the Canvas root folder remain directly
under `files/`.

Synchronization:

- writes files atomically
- avoids rewriting unchanged source data
- preserves raw JSON separately from rendered Markdown
- records inaccessible resources as warnings
- marks missing remote resources stale instead of deleting them
- never modifies or removes user transcripts

`canvascli status` checks local files and manifests without contacting Canvas.

## Use `canvasapi`

`canvasapi` does not read environment variables, configuration files, or the
current directory. Pass configuration explicitly:

```python
import os

from canvasapi import CanvasClient
from canvasapi.courses import get_course

client = CanvasClient(
    base_url="https://canvas.example.edu",
    token=os.environ["CANVAS_TOKEN"],
)
course = get_course(client, 123456)
```

Inject an HTTP session for tests. Pagination follows Canvas `Link` URLs as
opaque URLs.

## Development

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest -q
# or
uv run --extra test pytest -q
# or
nix flake check
```

Tests use mocked HTTP responses and do not contact Canvas.
