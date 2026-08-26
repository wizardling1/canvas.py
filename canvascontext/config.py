from __future__ import annotations

import json
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
class ContextConfig:
    base_url: str
    token: str
    output: Path
    courses: tuple[CourseSpec, ...]
    max_file_bytes: int = 500 * 1024 * 1024


def load_config(path: Path, *, require_token: bool = True) -> ContextConfig:
    path = path.expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Missing canvascontext config: {path}")
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)

    token = os.getenv("CANVAS_TOKEN", "")
    token_file = data.get("token_file")
    if not token and token_file:
        token_path = (path.parent / str(token_file)).resolve()
        try:
            token_data = json.loads(token_path.read_text())
            token = str(token_data.get("token") or "")
        except (OSError, ValueError, AttributeError) as exc:
            if require_token:
                raise SystemExit(f"Cannot load token file {token_path}: {exc}") from exc
    if require_token and not token:
        raise SystemExit(
            "Set CANVAS_TOKEN or configure token_file in canvascontext.toml"
        )

    course_specs = tuple(
        CourseSpec(
            id=int(course["id"]),
            slug=str(course["slug"]),
            name=str(course.get("name") or ""),
        )
        for course in data.get("courses", [])
    )
    if not course_specs:
        raise SystemExit("canvascontext.toml must define at least one [[courses]] entry")

    output = Path(str(data.get("output") or "courses")).expanduser()
    if not output.is_absolute():
        output = (path.parent / output).resolve()
    return ContextConfig(
        base_url=str(data.get("base_url") or DEFAULT_BASE_URL),
        token=token,
        output=output,
        courses=course_specs,
        max_file_bytes=int(data.get("max_file_bytes") or 500 * 1024 * 1024),
    )
