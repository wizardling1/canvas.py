# canvas.py

Python tools for downloading course content from Canvas using the [Canvas LMS REST
API](https://developerdocs.instructure.com/services/canvas):

- `canvasapi`: a Python library for interacting with the Canvas REST API
- `canvascli`: command-line course browser and batch downloader of course content

## `canvascli` usage 

`canvascli` is primarily intended to be used by batch downloading all course content into a
`canvascli`-managed directory or 'mirror'.

### Running `canvascli` 

The first step is to make sure you can run `canvascli`.

#### Requirements

- Python 3.10+
- `uv`, `nix`, or `pip`
- Canvas account and site URL, such as `https://canvas.example.edu`
- Canvas access token (see below)

#### uv

```bash
uv run canvascli --help
```

Prefix later commands with `uv run`.

#### Nix

```bash
nix run .#canvascli -- --help
nix develop
```

With `nix run`, pass command arguments after `--`:

```bash
nix run .#canvascli -- status
```

#### pip 

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Installation creates `canvascli` in the active environment, not `/usr/bin`.

### Access tokens

Once you can run `canvascli`, you need a canvas access token. 

Here are the steps to obtain one:

1. Sign in to Canvas.
2. Open **Account → Settings**.
3. Under **Approved Integrations**, select **New Access Token**.
4. Create and immediately copy the token. Canvas will not show it again.

For more details see the [Canvas authentication documentation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).

Once you have an access token, make sure the `CANVAS_TOKEN` environment variable is set to your
token.

### Creating a mirror

Run these commands to create a course mirror:

```bash
mkdir courses
cd courses
canvascli init --base-url https://canvas.example.edu
```

Initially the course mirror will have no courses. You can manually added courses by listing your
courses and their ids. Then add courses manually based on their ids.

```
canvascli list-courses 
canvascli add-course 123456 234567
```

You can also add all courses (including completed courses), or all active courses.

```bash
canvascli add-course --all-active
canvascli add-course --all
```

Remove courses without deleting their local files:

```bash
canvascli remove-course 123456
```

Synchronize all tracked courses or selected IDs to pull the latest changes:

```bash
canvascli sync
canvascli sync 123456 234567
```

Examine the status of all courses or individual courses:


```
canvascli status
canvascli status 123456
```

### Inspect and download

```bash
canvascli ls 123456
canvascli fetch 123456
canvascli fetch 123456 ./downloads
```

`ls` shows visible PDFs and assignments. `fetch` downloads course PDFs and
assignment files outside the mirror workflow.

Transcript support is experimental and may not work.

Add a transcript from an explicit file:

```bash
canvascli transcript add 123456 \
  --date 2026-08-28 \
  --file lecture.txt
```

## Mirror layout

Each course is stored under its generated name:

```text
course-name/
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

## `canvasapi` usage

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

