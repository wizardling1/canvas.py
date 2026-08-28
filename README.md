# canvas.py

A set of python tools for automatically downloading canvas content using the [Canvas LMS
REST API](https://developerdocs.instructure.com/services/canvas).

There are three python packages in this repo: 

- `canvasapi` is a python library for interacting with canvas rest api.
- `canvascli` is for interactively inspecting the canvas course and downloading single files.
- `canvasmirror` is for batch downloading all available files and content from a canvas course.

## Requirements

- Python 3.10 or newer
- The HTTPS base URL of the Canvas installation for your university or college, such as
  `https://wsu.instructure.com/`
- A Canvas access token (instructions to obtain this are below)

## Obtain a Canvas access token

Instructure documents manual tokens as a testing option. A multi-user
application must implement Canvas OAuth instead of asking users to create and
paste tokens. See Instructure's
[OAuth2 and manual token documentation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth).

To create a manual token:

1. Sign in to the institution's normal Canvas website.
2. Open **Account**, then **Settings**. The same page is normally available at
   `/profile` on the Canvas domain.
3. Find **Approved Integrations** and select **New Access Token**.
4. Enter a purpose, choose an expiration if the institution offers that field,
   and generate the token.
5. Copy the token immediately. Canvas does not display the complete value
   again after leaving the page.

If **New Access Token** is unavailable, the institution may restrict manual
token creation; contact its Canvas administrator. Never ask another user to
send you a manually generated token.

Canvas tokens are password-equivalent credentials. This project reads the
token exclusively from `CANVAS_TOKEN`; it never reads a token from TOML or
accepts one as a command-line argument. Read it into an interactive shell
without echoing it or storing it in shell history:

```bash
read -rsp "Canvas token: " CANVAS_TOKEN
printf '\n'
export CANVAS_TOKEN
```

When finished:

```bash
unset CANVAS_TOKEN
```

Do not put the token in source files, configuration files, shell commands,
`flake.nix`, or the Nix store. Secret managers, CI systems, and service
managers can inject `CANVAS_TOKEN` into the process environment at runtime.
The client sends it in the HTTP `Authorization: Bearer` header, as recommended
by the Canvas API documentation.

## Run the project

The examples in the application sections use the installed commands
`canvascli` and `canvasmirror`. When running directly from a checkout, choose
one of the following environments.

### uv

[uv](https://docs.astral.sh/uv/) reads `pyproject.toml`, creates an isolated
environment, and installs the project without requiring pip:

```bash
uv run canvascli --help
uv run canvasmirror --help
```

Prefix subsequent application commands with `uv run`, for example:

```bash
uv run canvasmirror sync
```

### Nix

The included flake supports `aarch64-darwin`, `aarch64-linux`,
`x86_64-darwin`, and `x86_64-linux` without pip or a mutable virtual
environment:

```bash
nix run .#canvascli -- --help
nix run .#canvasmirror -- --help
```

Pass application arguments after `--`:

```bash
nix run .#canvasmirror -- sync
```

For development from the source tree:

```bash
nix develop
python -m canvascli --help
python -m canvasmirror --help
```

The flake also provides `nix build` and `nix flake check`. `flake.lock` pins
the Nixpkgs revision.

### Python package frontends

Any PEP 517-compatible frontend can install the project. For example, with pip:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
```

This creates `canvascli` and `canvasmirror` inside the virtual environment. It
does not install anything into `/usr/bin` or require modification of the system
Python.

## Inspect a course with canvascli

`canvascli` is intended for manually exploring one course. It can select a
course, confirm the current selection, list visible assignments and PDFs, and
download course files.

Create `canvascli.toml` in the directory where commands will run. Set the base
URL to the Canvas website origin, without `/api/v1`:

```toml
base_url = "https://canvas.example.edu"
```

With `CANVAS_TOKEN` exported, list accessible courses and select one:

```bash
canvascli pick
```

The command atomically records the selected ID in `canvascli.toml`:

```toml
base_url = "https://canvas.example.edu"
course_id = 123456
```

The course ID can also be taken from a Canvas course URL ending in
`/courses/123456` and saved non-interactively:

```bash
canvascli pick --set-id 123456
```

Common commands are:

```bash
canvascli status
canvascli ls
canvascli fetch ./downloads
```

- `status` verifies that the selected course is accessible.
- `ls` lists visible PDFs and assignments with submission state.
- `fetch` downloads visible PDFs, assignment attachments, and Canvas file links.
  Conditional requests avoid downloading unchanged files when possible.

Use `canvascli --help` or `canvascli COMMAND --help` for complete command help.

## Mirror courses with canvasmirror

`canvasmirror` builds an incremental local representation of one or more
courses for offline browsing, search, and agent-assisted workflows.

Create `canvasmirror.toml` in the working directory:

```toml
base_url = "https://canvas.example.edu"
output = "courses"
max_file_bytes = 524288000

[[courses]]
id = 123456
slug = "example-course"
name = "Example Course"
```

Repeat `[[courses]]` for each course. Each course requires an integer `id` and
a filesystem-safe, unique `slug`; `name` is optional. `output` defaults to
`courses`, and `max_file_bytes` defaults to 500 MiB.

Synchronize every configured course:

```bash
canvasmirror sync
```

Synchronize only one course by slug:

```bash
canvasmirror sync example-course
```

Each mirrored course contains:

- `.canvas/raw/` with stable source JSON
- rendered Markdown for modules, assignments, pages, announcements, and
  discussions
- downloaded files discovered through visible Canvas resources
- `.canvas/manifest.sqlite` for incremental state
- `INDEX.md`, `COURSE.md`, `SYLLABUS.md`, and deterministic `STATUS.md`
- `transcripts/` for user-provided lecture transcripts

Synchronization writes files atomically and avoids rewriting unchanged source
content. Resources that disappear remotely are marked stale rather than
immediately deleted. Permission failures are recorded as warnings and do not
stop other courses. User-provided transcripts are never modified or removed by
synchronization.

Canvas may deny students access to course-wide Files or Pages indexes.
`canvasmirror` continues by discovering accessible resources through modules,
assignments, and page links.

Offline maintenance commands do not require `CANVAS_TOKEN`:

```bash
canvasmirror verify
canvasmirror status
canvasmirror transcript add example-course \
  --date 2026-08-25 < transcript.txt
```

- `verify` checks whether expected mirror files and manifest data exist.
- `status` regenerates status summaries from already mirrored raw data.
- `transcript add` stores a user-provided transcript with provenance metadata.

Use `canvasmirror --help` or `canvasmirror COMMAND --help` for full command
documentation.

## Use canvasapi as a library

`canvasapi` never reads the current directory, application configuration, or
environment variables. Callers resolve credentials and pass them explicitly:

```python
import os

from canvasapi import CanvasClient
from canvasapi.courses import get_course
from canvasapi.modules import list_modules

client = CanvasClient(
    base_url="https://canvas.example.edu",
    token=os.environ["CANVAS_TOKEN"],
)
course = get_course(client, 123456)
modules = list_modules(client, 123456)
```

Resource modules cover courses, modules and module items, assignments,
submissions, pages, files, announcements, discussions, and calendar events.
Inject a custom HTTP session into `CanvasClient` for tests. Paginated Canvas
`Link` URLs are followed as opaque URLs, as required by the
[Canvas pagination documentation](https://canvas.instructure.com/doc/api/file.pagination.html).

## Development and tests

Run the mocked test suite through any supported environment:

```bash
python3 -m pytest -q
# or
uv run --extra test pytest -q
# or
nix flake check
```

Normal tests do not contact Canvas. Live integration checks require a
developer-supplied token and are intentionally separate from the mocked suite.
