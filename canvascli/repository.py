from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .diagnostics import CLIError

REPOSITORY_FILENAME = "canvasmirror.json"
SCHEMA_VERSION = 1
DEFAULT_MAX_FILE_BYTES = 500 * 1024 * 1024


@dataclass(frozen=True)
class CourseRecord:
    id: int
    slug: str
    name: str
    code: str = ""
    state: str = ""
    term: str = ""

    @classmethod
    def from_canvas(cls, course: dict[str, Any], *, slug: str) -> "CourseRecord":
        term = course.get("term") or {}
        return cls(
            id=int(course["id"]),
            slug=slug,
            name=str(course.get("name") or course.get("course_code") or "Unnamed course"),
            code=str(course.get("course_code") or ""),
            state=str(course.get("workflow_state") or ""),
            term=str(term.get("name") or ""),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "code": self.code,
            "state": self.state,
            "term": self.term,
        }


@dataclass(frozen=True)
class CanvasRepository:
    root: Path
    base_url: str
    courses: tuple[CourseRecord, ...] = ()
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    version: int = SCHEMA_VERSION

    @property
    def path(self) -> Path:
        return self.root / REPOSITORY_FILENAME

    def with_courses(self, courses: Iterable[CourseRecord]) -> "CanvasRepository":
        return replace(self, courses=tuple(sorted(courses, key=lambda item: item.id)))

    def course(self, course_id: int) -> CourseRecord:
        for course in self.courses:
            if course.id == course_id:
                return course
        raise CLIError(
            f"course {course_id} is not tracked by this repository",
            f"run `canvascli add-course {course_id}`",
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "canvas": {"base_url": self.base_url},
            "settings": {"max_file_bytes": self.max_file_bytes},
            "courses": [course.to_json() for course in self.courses],
        }


def normalize_base_url(value: str) -> str:
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CLIError(
            f"invalid Canvas base URL: {value!r}",
            "use the Canvas website origin, such as https://canvas.example.edu",
        )
    if parsed.query or parsed.fragment:
        raise CLIError("Canvas base URL must not contain a query or fragment")
    if parsed.username or parsed.password:
        raise CLIError("Canvas base URL must not contain credentials")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1"):
        raise CLIError(
            "Canvas base URL must not include /api/v1",
            f"use {urlunsplit((parsed.scheme, parsed.netloc, path[:-7], '', '')).rstrip('/')}",
        )
    if path:
        raise CLIError(
            "Canvas base URL must be the website origin without a path",
            f"use {urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))}",
        )
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def course_slug(course: dict[str, Any], used: set[str]) -> str:
    source = str(course.get("course_code") or course.get("name") or "course")
    slug = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-") or "course"
    candidate = slug
    if candidate in used:
        candidate = f"{slug}-{int(course['id'])}"
    counter = 2
    while candidate in used:
        candidate = f"{slug}-{int(course['id'])}-{counter}"
        counter += 1
    return candidate


def find_repository_path(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / REPOSITORY_FILENAME
        if candidate.is_file():
            return candidate
    return None


def discover_repository(start: Path | None = None) -> CanvasRepository:
    path = find_repository_path(start)
    if path is None:
        raise CLIError(
            f"no {REPOSITORY_FILENAME} found in this directory or its parents",
            "run `canvascli init --base-url URL` in the intended mirror root",
        )
    return load_repository(path)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CLIError(f"{label} must be a JSON object")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise CLIError(f"{label} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CLIError(f"{label} must be a positive integer") from exc
    if result <= 0:
        raise CLIError(f"{label} must be a positive integer")
    return result


def load_repository(path: Path) -> CanvasRepository:
    path = path.expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CLIError(f"repository metadata does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise CLIError(f"cannot read repository metadata {path}: {exc}", exit_code=5) from exc

    root = _object(data, str(path))
    version = root.get("version")
    if version != SCHEMA_VERSION:
        raise CLIError(
            f"unsupported repository schema version {version!r}; supported version is {SCHEMA_VERSION}"
        )
    canvas = _object(root.get("canvas"), "canvas")
    base_url = normalize_base_url(str(canvas.get("base_url") or ""))
    settings = _object(root.get("settings"), "settings")
    max_file_bytes = _positive_integer(
        settings.get("max_file_bytes"), "settings.max_file_bytes"
    )
    raw_courses = root.get("courses")
    if not isinstance(raw_courses, list):
        raise CLIError("courses must be a JSON array")

    courses: list[CourseRecord] = []
    ids: set[int] = set()
    slugs: set[str] = set()
    for index, raw_course in enumerate(raw_courses):
        item = _object(raw_course, f"courses[{index}]")
        course_id = _positive_integer(item.get("id"), f"courses[{index}].id")
        slug = str(item.get("slug") or "")
        if not slug or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise CLIError(f"courses[{index}].slug is not filesystem-safe: {slug!r}")
        if course_id in ids:
            raise CLIError(f"duplicate course ID in repository: {course_id}")
        if slug in slugs:
            raise CLIError(f"duplicate course slug in repository: {slug}")
        ids.add(course_id)
        slugs.add(slug)
        courses.append(
            CourseRecord(
                id=course_id,
                slug=slug,
                name=str(item.get("name") or "Unnamed course"),
                code=str(item.get("code") or ""),
                state=str(item.get("state") or ""),
                term=str(item.get("term") or ""),
            )
        )
    return CanvasRepository(
        root=path.parent,
        base_url=base_url,
        courses=tuple(courses),
        max_file_bytes=max_file_bytes,
        version=version,
    )


def save_repository(repository: CanvasRepository) -> None:
    if repository.version != SCHEMA_VERSION:
        raise CLIError(
            f"cannot write unsupported repository schema version {repository.version!r}"
        )
    if repository.max_file_bytes <= 0:
        raise CLIError("settings.max_file_bytes must be a positive integer")
    if normalize_base_url(repository.base_url) != repository.base_url:
        raise CLIError("repository Canvas base URL is not normalized")
    ids = [course.id for course in repository.courses]
    slugs = [course.slug for course in repository.courses]
    if any(course_id <= 0 for course_id in ids):
        raise CLIError("cannot write repository with non-positive course IDs")
    if any(
        not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
        for slug in slugs
    ):
        raise CLIError("cannot write repository with unsafe course slugs")
    if len(ids) != len(set(ids)):
        raise CLIError("cannot write repository with duplicate course IDs")
    if len(slugs) != len(set(slugs)):
        raise CLIError("cannot write repository with duplicate course slugs")
    path = repository.path
    temporary = path.with_name(f".{path.name}.tmp")
    encoded = json.dumps(
        repository.to_json(), indent=2, ensure_ascii=False, sort_keys=False
    ) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CLIError(f"cannot write repository metadata {path}: {exc}", exit_code=5) from exc


def initialize_repository(directory: Path, base_url: str) -> CanvasRepository:
    root = directory.expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise CLIError(f"repository location is not a directory: {root}")
    existing = find_repository_path(root)
    if existing is not None:
        raise CLIError(f"already inside Canvas repository at {existing.parent}")
    path = root / REPOSITORY_FILENAME
    if path.exists():
        raise CLIError(f"repository metadata already exists: {path}")
    repository = CanvasRepository(root=root, base_url=normalize_base_url(base_url))
    save_repository(repository)
    return repository
