from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from canvasapi import (
    CanvasAuthorizationError,
    CanvasClient,
    CanvasNotFoundError,
)
from canvasapi.announcements import list_announcements
from canvasapi.assignments import list_assignments
from canvasapi.calendar import list_calendar_events
from canvasapi.courses import get_course
from canvasapi.discussions import list_discussions
from canvasapi.files import get_file, list_course_files, open_download
from canvasapi.folders import get_folder, list_course_folders
from canvasapi.modules import list_modules
from canvasapi.pages import get_page, list_pages
from canvasapi.submissions import list_my_submissions

from ..repository import CourseRecord
from .manifest import Manifest
from .render import (
    frontmatter,
    html_to_markdown,
    render_announcement,
    render_assignment,
    render_course,
    render_discussion,
    render_module,
    render_page,
    resource_path,
    safe_slug,
)
from .status import generate_status
from .storage import (
    content_hash,
    stable_canvas_data,
    write_json_if_changed,
    write_text_if_changed,
)

T = TypeVar("T")
FILE_LINK_RE = re.compile(
    r"(?:/api/v1)?/files/(\d+)(?:/download|/preview|\b)", re.IGNORECASE
)
UNSAFE_PATH_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_path_component(value: str, fallback: str) -> str:
    """Return one safe, human-readable local path component."""
    component = UNSAFE_PATH_CHARS_RE.sub("_", value).strip()
    component = component.rstrip(".")
    if component in {"", ".", ".."}:
        component = fallback
    if len(component) > 180:
        source = Path(component)
        suffix = source.suffix[:20]
        component = f"{source.stem[: 180 - len(suffix)]}{suffix}"
    return component


def folder_relative_path(
    folder: dict[str, Any] | None, folder_id: int | None
) -> Path:
    """Return a safe path below ``files/`` for a Canvas folder."""
    if not folder:
        label = str(folder_id) if folder_id is not None else "unknown"
        return Path(f"_unresolved-folder-{label}")
    full_name = str(folder.get("full_name") or "")
    parts = [part for part in full_name.split("/") if part]
    if not parts:
        label = str(folder_id) if folder_id is not None else "unknown"
        return Path(f"_unresolved-folder-{label}")
    # Canvas includes the context root (normally "course files") in full_name.
    relative_parts = parts[1:]
    return Path(
        *(safe_path_component(part, "unnamed-folder") for part in relative_parts)
    )


def file_relative_path(
    file_id: int,
    metadata: dict[str, Any],
    folders_by_id: dict[int, dict[str, Any]],
    used_paths: dict[str, int],
) -> Path:
    """Choose a collision-safe local path retaining the Canvas folder tree."""
    raw_folder_id = metadata.get("folder_id")
    try:
        folder_id = int(raw_folder_id) if raw_folder_id is not None else None
    except (TypeError, ValueError):
        folder_id = None
    directory = folder_relative_path(
        folders_by_id.get(folder_id) if folder_id is not None else None,
        folder_id,
    )
    raw_name = str(
        metadata.get("display_name")
        or metadata.get("filename")
        or f"file-{file_id}"
    )
    filename = safe_path_component(raw_name, f"file-{file_id}")
    relative = Path("files") / directory / filename
    collision_key = relative.as_posix().casefold()
    if collision_key in used_paths and used_paths[collision_key] != file_id:
        path = Path(filename)
        filename = f"{path.stem}__{file_id}{path.suffix}"
        relative = Path("files") / directory / filename
        collision_key = relative.as_posix().casefold()
    used_paths[collision_key] = file_id
    return relative


class CourseMirror:
    def __init__(
        self,
        *,
        client: CanvasClient,
        course: CourseRecord,
        output_root: Path,
        max_file_bytes: int,
    ) -> None:
        self.client = client
        self.course_spec = course
        self.root = output_root / course.slug
        self.internal = self.root / ".canvas"
        self.raw = self.internal / "raw"
        self.max_file_bytes = max_file_bytes
        self.warnings: list[str] = []
        self.changed_files = 0

    def _optional(self, label: str, operation: Callable[[], T], default: T) -> T:
        try:
            return operation()
        except (CanvasAuthorizationError, CanvasNotFoundError) as exc:
            self.warnings.append(f"{label}: {exc}")
            return default

    def _record_text(
        self,
        manifest: Manifest,
        run_id: int,
        *,
        kind: str,
        canvas_id: str | int,
        relative_path: Path,
        text: str,
        updated_at: str | None = None,
        source_url: str | None = None,
    ) -> None:
        destination = self.root / relative_path
        self.changed_files += int(write_text_if_changed(destination, text))
        manifest.record(
            run_id,
            kind=kind,
            canvas_id=canvas_id,
            local_path=relative_path.as_posix(),
            updated_at=updated_at,
            content_hash=content_hash(text.encode()),
            source_url=source_url,
        )

    @staticmethod
    def _file_ids_from_html(source: str | None) -> set[int]:
        return {
            int(match.group(1)) for match in FILE_LINK_RE.finditer(source or "")
        }

    def _collect_file_metadata(
        self,
        modules: list[dict[str, Any]],
        assignments: list[dict[str, Any]],
        pages: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        file_ids: set[int] = set()
        for module in modules:
            for item in module.get("items") or []:
                if item.get("type") == "File" and item.get("content_id"):
                    file_ids.add(int(item["content_id"]))
        for assignment in assignments:
            for attachment in assignment.get("attachments") or []:
                if attachment.get("id") is not None:
                    file_ids.add(int(attachment["id"]))
            file_ids.update(
                self._file_ids_from_html(assignment.get("description"))
            )
        for page in pages:
            file_ids.update(self._file_ids_from_html(page.get("body")))

        metadata_by_id: dict[int, dict[str, Any]] = {}
        course_files = self._optional(
            "course files",
            lambda: list_course_files(self.client, self.course_spec.id),
            [],
        )
        for metadata in course_files:
            if metadata.get("id") is not None:
                metadata_by_id[int(metadata["id"])] = metadata
        for file_id in sorted(file_ids):
            if file_id in metadata_by_id:
                continue
            metadata = self._optional(
                f"file {file_id}",
                lambda file_id=file_id: get_file(self.client, file_id),
                {},
            )
            if metadata:
                metadata_by_id[file_id] = metadata
        return metadata_by_id

    def _collect_folder_metadata(
        self, metadata_by_id: dict[int, dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        folders_by_id: dict[int, dict[str, Any]] = {}
        course_folders = self._optional(
            "course folders",
            lambda: list_course_folders(self.client, self.course_spec.id),
            [],
        )
        for folder in course_folders:
            if folder.get("id") is not None:
                folders_by_id[int(folder["id"])] = folder

        folder_ids: set[int] = set()
        for metadata in metadata_by_id.values():
            try:
                if metadata.get("folder_id") is not None:
                    folder_ids.add(int(metadata["folder_id"]))
            except (TypeError, ValueError):
                self.warnings.append(
                    f"file {metadata.get('id')}: invalid folder_id={metadata.get('folder_id')!r}"
                )
        for folder_id in sorted(folder_ids - folders_by_id.keys()):
            folder = self._optional(
                f"folder {folder_id}",
                lambda folder_id=folder_id: get_folder(self.client, folder_id),
                {},
            )
            if folder and folder.get("id") is not None:
                folders_by_id[int(folder["id"])] = folder
        return folders_by_id

    def _download_files(
        self,
        manifest: Manifest,
        run_id: int,
        metadata_by_id: dict[int, dict[str, Any]],
        folders_by_id: dict[int, dict[str, Any]],
    ) -> dict[int, Path]:
        local_files: dict[int, Path] = {}
        etag_directory = self.internal / "etags"
        skipped: list[dict[str, Any]] = []
        used_paths: dict[str, int] = {}
        for file_id, metadata in sorted(metadata_by_id.items()):
            raw_name = str(
                metadata.get("display_name")
                or metadata.get("filename")
                or f"file-{file_id}"
            )
            relative = file_relative_path(
                file_id, metadata, folders_by_id, used_paths
            )
            destination = self.root / relative
            size = int(metadata.get("size") or 0)
            if self.max_file_bytes and size > self.max_file_bytes:
                skipped.append(
                    {
                        "id": file_id,
                        "name": raw_name,
                        "size": size,
                        "reason": f"exceeds max_file_bytes={self.max_file_bytes}",
                    }
                )
                continue
            if not (metadata.get("download_url") or metadata.get("url")):
                skipped.append(
                    {"id": file_id, "name": raw_name, "reason": "no download URL"}
                )
                continue

            etag_path = etag_directory / str(file_id)
            headers: dict[str, str] = {}
            if etag_path.exists() and destination.exists():
                etag = etag_path.read_text().strip()
                if etag:
                    headers["If-None-Match"] = etag
            try:
                response = open_download(self.client, metadata, headers=headers)
                with response:
                    if response.status_code != 304:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary = destination.with_suffix(destination.suffix + ".part")
                        with temporary.open("wb") as output:
                            for chunk in response.iter_content(8192):
                                if chunk:
                                    output.write(chunk)
                        temporary.replace(destination)
                        self.changed_files += 1
                        etag = response.headers.get("ETag")
                        if etag:
                            etag_path.parent.mkdir(parents=True, exist_ok=True)
                            etag_path.write_text(etag)
            except (CanvasAuthorizationError, CanvasNotFoundError) as exc:
                skipped.append(
                    {"id": file_id, "name": raw_name, "reason": str(exc)}
                )
                continue

            if destination.exists():
                local_files[file_id] = relative
            digest = content_hash(destination.read_bytes()) if destination.exists() else None
            manifest.record(
                run_id,
                kind="file",
                canvas_id=file_id,
                local_path=relative.as_posix(),
                updated_at=metadata.get("updated_at") or metadata.get("modified_at"),
                content_hash=digest,
                source_url=metadata.get("url") or metadata.get("download_url"),
            )
        write_json_if_changed(self.internal / "skipped-files.json", skipped)
        return local_files

    def sync(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        synced_at = datetime.now(timezone.utc).isoformat()
        with Manifest(self.internal / "manifest.sqlite") as manifest:
            run_id = manifest.begin()
            try:
                course = get_course(self.client, self.course_spec.id)
                modules = self._optional(
                    "modules",
                    lambda: list_modules(self.client, self.course_spec.id),
                    [],
                )
                assignments = self._optional(
                    "assignments",
                    lambda: list_assignments(self.client, self.course_spec.id),
                    [],
                )
                submissions = self._optional(
                    "submissions",
                    lambda: list_my_submissions(self.client, self.course_spec.id),
                    [],
                )
                pages = self._optional(
                    "pages",
                    lambda: list_pages(self.client, self.course_spec.id),
                    [],
                )
                pages_by_id = {
                    str(page.get("page_id") or page.get("url")): page for page in pages
                }
                known_page_urls = {str(page.get("url") or "") for page in pages}
                for module in modules:
                    for item in module.get("items") or []:
                        page_url = item.get("url")
                        if item.get("type") != "Page" or not page_url:
                            continue
                        page_slug = str(page_url).rstrip("/").rsplit("/", 1)[-1]
                        if page_slug in known_page_urls:
                            continue
                        page = self._optional(
                            f"module page {item.get('title') or page_slug}",
                            lambda page_url=page_url: get_page(self.client, str(page_url)),
                            {},
                        )
                        if page:
                            pages_by_id[str(page.get("page_id") or page.get("url"))] = page
                            known_page_urls.add(str(page.get("url") or page_slug))
                pages = list(pages_by_id.values())
                announcements = self._optional(
                    "announcements",
                    lambda: list_announcements(self.client, self.course_spec.id),
                    [],
                )
                discussions = self._optional(
                    "discussions",
                    lambda: list_discussions(self.client, self.course_spec.id),
                    [],
                )
                calendar_events = self._optional(
                    "calendar",
                    lambda: list_calendar_events(self.client, self.course_spec.id),
                    [],
                )

                submission_map = {
                    int(submission["assignment_id"]): submission
                    for submission in submissions
                    if submission.get("assignment_id") is not None
                }
                for assignment in assignments:
                    if not assignment.get("submission") and assignment.get("id"):
                        assignment["submission"] = submission_map.get(
                            int(assignment["id"]), {}
                        )

                raw_values = {
                    "course": course,
                    "modules": modules,
                    "assignments": assignments,
                    "submissions": submissions,
                    "pages": pages,
                    "announcements": announcements,
                    "discussions": discussions,
                    "calendar-events": calendar_events,
                }
                for name, value in raw_values.items():
                    self.changed_files += int(
                        write_json_if_changed(
                            self.raw / f"{name}.json", stable_canvas_data(value)
                        )
                    )

                metadata_by_id = self._collect_file_metadata(
                    modules, assignments, pages
                )
                folders_by_id = self._collect_folder_metadata(metadata_by_id)
                self.changed_files += int(
                    write_json_if_changed(
                        self.raw / "files.json",
                        stable_canvas_data(list(metadata_by_id.values())),
                    )
                )
                self.changed_files += int(
                    write_json_if_changed(
                        self.raw / "folders.json",
                        stable_canvas_data(
                            sorted(
                                folders_by_id.values(),
                                key=lambda folder: int(folder.get("id") or 0),
                            )
                        ),
                    )
                )
                local_files = self._download_files(
                    manifest, run_id, metadata_by_id, folders_by_id
                )

                self._record_text(
                    manifest,
                    run_id,
                    kind="course",
                    canvas_id=course["id"],
                    relative_path=Path("COURSE.md"),
                    text=render_course(course, synced_at),
                    updated_at=course.get("updated_at"),
                    source_url=course.get("html_url"),
                )
                syllabus = html_to_markdown(course.get("syllabus_body"))
                syllabus_text = (
                    frontmatter(
                        canvas_type="syllabus",
                        course_id=course["id"],
                    )
                    + "# Syllabus\n\n"
                    + (syllabus or "No syllabus body is visible in Canvas.")
                    + "\n"
                )
                self._record_text(
                    manifest,
                    run_id,
                    kind="syllabus",
                    canvas_id=course["id"],
                    relative_path=Path("SYLLABUS.md"),
                    text=syllabus_text,
                )

                module_links: list[str] = []
                for position, module in enumerate(modules, 1):
                    relative = resource_path(
                        "modules",
                        f"{position:02d}-{module.get('id')}",
                        str(module.get("name") or "module"),
                    )
                    module_links.append(
                        f"- [{module.get('name') or 'Module'}]({relative.as_posix()})"
                    )
                    self._record_text(
                        manifest,
                        run_id,
                        kind="module",
                        canvas_id=module["id"],
                        relative_path=relative,
                        text=render_module(
                            module, course["id"], synced_at, local_files
                        ),
                        updated_at=module.get("updated_at"),
                    )

                assignment_links: list[str] = []
                for assignment in assignments:
                    relative = resource_path(
                        "assignments",
                        assignment["id"],
                        str(assignment.get("name") or "assignment"),
                    )
                    assignment_links.append(
                        f"- [{assignment.get('name') or 'Assignment'}]({relative.as_posix()})"
                    )
                    self._record_text(
                        manifest,
                        run_id,
                        kind="assignment",
                        canvas_id=assignment["id"],
                        relative_path=relative,
                        text=render_assignment(
                            assignment, course["id"], synced_at
                        ),
                        updated_at=assignment.get("updated_at"),
                        source_url=assignment.get("html_url"),
                    )

                page_links: list[str] = []
                for page in pages:
                    page_id = page.get("page_id") or page.get("url")
                    relative = resource_path(
                        "pages", page_id, str(page.get("title") or "page")
                    )
                    page_links.append(
                        f"- [{page.get('title') or 'Page'}]({relative.as_posix()})"
                    )
                    self._record_text(
                        manifest,
                        run_id,
                        kind="page",
                        canvas_id=page_id,
                        relative_path=relative,
                        text=render_page(page, course["id"], synced_at),
                        updated_at=page.get("updated_at"),
                        source_url=page.get("html_url"),
                    )

                announcement_links: list[str] = []
                for announcement in announcements:
                    relative = resource_path(
                        "announcements",
                        announcement["id"],
                        str(announcement.get("title") or "announcement"),
                    )
                    announcement_links.append(
                        f"- [{announcement.get('title') or 'Announcement'}]({relative.as_posix()})"
                    )
                    self._record_text(
                        manifest,
                        run_id,
                        kind="announcement",
                        canvas_id=announcement["id"],
                        relative_path=relative,
                        text=render_announcement(
                            announcement, course["id"], synced_at
                        ),
                        updated_at=announcement.get("updated_at")
                        or announcement.get("posted_at"),
                        source_url=announcement.get("html_url"),
                    )

                discussion_links: list[str] = []
                for discussion in discussions:
                    relative = resource_path(
                        "discussions",
                        discussion["id"],
                        str(discussion.get("title") or "discussion"),
                    )
                    discussion_links.append(
                        f"- [{discussion.get('title') or 'Discussion'}]({relative.as_posix()})"
                    )
                    self._record_text(
                        manifest,
                        run_id,
                        kind="discussion",
                        canvas_id=discussion["id"],
                        relative_path=relative,
                        text=render_discussion(
                            discussion, course["id"], synced_at
                        ),
                        updated_at=discussion.get("last_reply_at")
                        or discussion.get("posted_at"),
                        source_url=discussion.get("html_url"),
                    )

                status_text = generate_status(
                    course=course,
                    assignments=assignments,
                    submissions=submissions,
                    modules=modules,
                    announcements=announcements,
                    synced_at=synced_at,
                )
                self._record_text(
                    manifest,
                    run_id,
                    kind="generated-status",
                    canvas_id=course["id"],
                    relative_path=Path("STATUS.md"),
                    text=status_text,
                )
                index = [
                    f"# {course.get('name') or self.course_spec.slug}",
                    "",
                    "- [Course](COURSE.md)",
                    "- [Current status](STATUS.md)",
                    "- [Syllabus](SYLLABUS.md)",
                    "",
                    "## Modules",
                    "",
                    *(module_links or ["No visible modules."]),
                    "",
                    "## Assignments",
                    "",
                    *(assignment_links or ["No visible assignments."]),
                    "",
                    "## Pages",
                    "",
                    *(page_links or ["No visible pages."]),
                    "",
                    "## Announcements",
                    "",
                    *(announcement_links or ["No visible announcements."]),
                    "",
                    "## Discussions",
                    "",
                    *(discussion_links or ["No visible discussions."]),
                    "",
                    "## Lecture transcripts",
                    "",
                    "User-provided transcripts are stored in `transcripts/`.",
                    "",
                ]
                self._record_text(
                    manifest,
                    run_id,
                    kind="generated-index",
                    canvas_id=course["id"],
                    relative_path=Path("INDEX.md"),
                    text="\n".join(index),
                )

                result = {
                    "course_id": self.course_spec.id,
                    "slug": self.course_spec.slug,
                    "name": course.get("name"),
                    "synced_at": synced_at,
                    "changed_files": self.changed_files,
                    "counts": {
                        "modules": len(modules),
                        "assignments": len(assignments),
                        "submissions": len(submissions),
                        "pages": len(pages),
                        "announcements": len(announcements),
                        "discussions": len(discussions),
                        "calendar_events": len(calendar_events),
                        "files": len(metadata_by_id),
                        "folders": len(folders_by_id),
                    },
                    "warnings": self.warnings,
                }
                self.changed_files += int(
                    write_json_if_changed(self.internal / "last-sync.json", result)
                )
                result["changed_files"] = self.changed_files
                manifest.finish(run_id, success=True)
                return result
            except Exception as exc:
                manifest.finish(run_id, success=False, error=str(exc))
                raise


def build_root_index(output_root: Path, results: list[dict[str, Any]]) -> None:
    lines = ["# Canvas course context", ""]
    for result in results:
        slug = result["slug"]
        lines.extend(
            [
                f"## [{result.get('name') or slug}]({slug}/INDEX.md)",
                "",
                f"- Canvas course ID: {result['course_id']}",
                f"- Last sync: {result['synced_at']}",
                f"- Changed files: {result['changed_files']}",
                "",
            ]
        )
    write_text_if_changed(output_root / "INDEX.md", "\n".join(lines))
