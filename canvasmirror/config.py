from __future__ import annotations

import os
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass
from pathlib import Path

from canvasapi import DEFAULT_BASE_URL


@dataclass(frozen=True)
class CourseSpec:
    id: int
    slug: str
    name: str = ""


@dataclass(frozen=True)
class MirrorConfig:
    base_url: str
    token: str
    output: Path
    courses: tuple[CourseSpec, ...]
    max_file_bytes: int = 500 * 1024 * 1024


def load_config(path: Path, *, require_token: bool = True) -> MirrorConfig:
    path = path.expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Missing canvasmirror config: {path}")
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)

    token = os.getenv("CANVAS_TOKEN", "")
    if require_token and not token:
        raise SystemExit("Set CANVAS_TOKEN before contacting Canvas")

    course_specs = tuple(
        CourseSpec(
            id=int(course["id"]),
            slug=str(course["slug"]),
            name=str(course.get("name") or ""),
        )
        for course in data.get("courses", [])
    )
    if not course_specs:
        raise SystemExit("canvasmirror.toml must define at least one [[courses]] entry")

    output = Path(str(data.get("output") or "courses")).expanduser()
    if not output.is_absolute():
        output = (path.parent / output).resolve()
    return MirrorConfig(
        base_url=str(data.get("base_url") or DEFAULT_BASE_URL),
        token=token,
        output=output,
        courses=course_specs,
        max_file_bytes=int(data.get("max_file_bytes") or 500 * 1024 * 1024),
    )
