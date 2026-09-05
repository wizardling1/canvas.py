from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from canvasapi import CanvasAuthorizationError
from canvascli.mirror.mirror import (
    CourseMirror,
    build_root_index,
    file_relative_path,
    folder_relative_path,
    safe_path_component,
)
from canvascli.mirror.manifest import Manifest
from canvascli.mirror.render import html_to_markdown
from canvascli.mirror.status import generate_status
from canvascli.mirror.storage import stable_canvas_data, write_text_if_changed
from canvascli.mirror.transcripts import add_transcript
from canvascli.repository import CourseRecord


def test_html_to_markdown_preserves_links_and_lists() -> None:
    result = html_to_markdown(
        '<h2>Read</h2><ul><li><a href="https://example.test">Chapter 1</a></li></ul>'
    )

    assert "## Read" in result
    assert "- [Chapter 1](https://example.test)" in result


def test_write_text_if_changed_preserves_unchanged_file(tmp_path) -> None:
    path = tmp_path / "note.md"

    assert write_text_if_changed(path, "hello\n") is True
    first_mtime = path.stat().st_mtime_ns
    assert write_text_if_changed(path, "hello\n") is False
    assert path.stat().st_mtime_ns == first_mtime


def test_permission_failure_is_recorded_as_warning(tmp_path) -> None:
    mirror = CourseMirror(
        client=object(),
        course=CourseRecord(id=7, slug="course-seven", name="Course Seven"),
        output_root=tmp_path,
        max_file_bytes=100,
    )

    def denied() -> list[object]:
        raise CanvasAuthorizationError("denied")

    assert mirror._optional("course files", denied, []) == []
    assert mirror.warnings == ["course files: denied"]


def test_file_path_retains_canvas_folder_hierarchy() -> None:
    folders = {
        10: {
            "id": 10,
            "full_name": "course files/Week 1/Lecture Notes",
        }
    }

    relative = file_relative_path(
        99,
        {"id": 99, "folder_id": 10, "display_name": "Introduction.pdf"},
        folders,
        {},
    )

    assert relative.as_posix() == "files/Week 1/Lecture Notes/Introduction.pdf"


def test_file_path_uses_visible_fallback_for_unresolved_folder() -> None:
    relative = file_relative_path(
        99,
        {"id": 99, "folder_id": 10, "display_name": "Introduction.pdf"},
        {},
        {},
    )

    assert relative.as_posix() == "files/_unresolved-folder-10/Introduction.pdf"


def test_file_paths_are_safe_and_collision_resistant() -> None:
    assert safe_path_component("../unsafe:name", "fallback") == ".._unsafe_name"
    assert folder_relative_path(
        {"full_name": "course files/../Week 1"}, 10
    ).as_posix() == "unnamed-folder/Week 1"
    used: dict[str, int] = {}
    first = file_relative_path(
        1,
        {"folder_id": 10, "display_name": "Notes.pdf"},
        {10: {"full_name": "course files/Week 1"}},
        used,
    )
    second = file_relative_path(
        2,
        {"folder_id": 10, "display_name": "notes.PDF"},
        {10: {"full_name": "course files/Week 1"}},
        used,
    )

    assert first.as_posix() == "files/Week 1/Notes.pdf"
    assert second.as_posix() == "files/Week 1/notes__2.PDF"


def test_download_writes_bytes_under_canvas_folder_path(tmp_path, monkeypatch) -> None:
    mirror = CourseMirror(
        client=object(),
        course=CourseRecord(id=7, slug="course-seven", name="Course Seven"),
        output_root=tmp_path,
        max_file_bytes=100,
    )

    class Download:
        status_code = 200
        headers = {"ETag": '"test-etag"'}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_content(self, _size):
            yield b"lecture notes"

    monkeypatch.setattr(
        "canvascli.mirror.mirror.open_download",
        lambda *_args, **_kwargs: Download(),
    )
    metadata = {
        99: {
            "id": 99,
            "folder_id": 10,
            "display_name": "Introduction.pdf",
            "size": 13,
            "url": "https://canvas.example/files/99/download",
        }
    }
    folders = {
        10: {"id": 10, "full_name": "course files/Week 1/Lecture Notes"}
    }
    with Manifest(tmp_path / "manifest.sqlite") as manifest:
        run_id = manifest.begin()
        local_files = mirror._download_files(
            manifest, run_id, metadata, folders
        )
        manifest.finish(run_id, success=True)

    expected = Path("files/Week 1/Lecture Notes/Introduction.pdf")
    assert local_files == {99: expected}
    assert (tmp_path / "course-seven" / expected).read_bytes() == b"lecture notes"


def test_folder_collection_falls_back_to_individual_lookup(
    tmp_path, monkeypatch
) -> None:
    mirror = CourseMirror(
        client=object(),
        course=CourseRecord(id=7, slug="course-seven", name="Course Seven"),
        output_root=tmp_path,
        max_file_bytes=100,
    )

    def denied(*_args):
        raise CanvasAuthorizationError("denied")

    monkeypatch.setattr("canvascli.mirror.mirror.list_course_folders", denied)
    monkeypatch.setattr(
        "canvascli.mirror.mirror.get_folder",
        lambda _client, folder_id: {
            "id": folder_id,
            "full_name": "course files/Recovered",
        },
    )

    folders = mirror._collect_folder_metadata(
        {99: {"id": 99, "folder_id": 10}}
    )

    assert folders[10]["full_name"] == "course files/Recovered"
    assert mirror.warnings == ["course folders: denied"]


def test_stable_canvas_data_removes_only_ephemeral_fields() -> None:
    value = {
        "missing": True,
        "seconds_late": 123,
        "attachment": {
            "url": "https://canvas.example/files/1",
            "canvadoc_session_url": "https://preview.example/session",
        },
    }

    assert stable_canvas_data(value) == {
        "missing": True,
        "attachment": {"url": "https://canvas.example/files/1"},
    }


def test_add_transcript_creates_provenance_document(tmp_path) -> None:
    destination = add_transcript(
        course_root=tmp_path,
        course_id=420,
        date="2026-08-25",
        text="Today we discussed vector spaces.",
    )

    assert destination.name == "2026-08-25.md"
    document = destination.read_text()
    assert 'canvas_type: "lecture_transcript"' in document
    assert 'title: "2026-08-25"' in document
    assert "# 2026-08-25" in document
    assert "Today we discussed vector spaces." in document


def test_root_index_keeps_all_provided_courses_without_editing_agent_instructions(
    tmp_path,
) -> None:
    instructions = "# Hand-maintained agent instructions\n"
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(instructions)

    build_root_index(
        tmp_path,
        [
            {
                "slug": "math-300",
                "name": "Mathematical Computing",
                "course_id": 300,
                "synced_at": "now",
                "changed_files": 1,
            },
            {
                "slug": "math-420",
                "name": "Linear Algebra",
                "course_id": 420,
                "synced_at": "now",
                "changed_files": 1,
            },
        ],
    )

    index = (tmp_path / "INDEX.md").read_text()
    assert "math-300/INDEX.md" in index
    assert "math-420/INDEX.md" in index
    assert agents_path.read_text() == instructions


def test_status_classifies_missing_upcoming_and_submitted() -> None:
    now = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    assignments = [
        {
            "id": 1,
            "name": "Missing work",
            "published": True,
            "due_at": "2026-08-24T12:00:00Z",
            "submission": {"workflow_state": "unsubmitted", "missing": True},
        },
        {
            "id": 2,
            "name": "Upcoming work",
            "published": True,
            "due_at": "2026-08-27T12:00:00Z",
            "submission": {"workflow_state": "unsubmitted"},
        },
        {
            "id": 3,
            "name": "Waiting for grade",
            "published": True,
            "due_at": "2026-08-28T12:00:00Z",
            "submission": {"workflow_state": "submitted"},
        },
    ]

    result = generate_status(
        course={"id": 9, "name": "Test course"},
        assignments=assignments,
        submissions=[],
        modules=[],
        announcements=[],
        synced_at="2026-08-25T12:00:00Z",
        now=now,
    )

    missing_section = result.split("## Missing", 1)[1].split("## Overdue", 1)[0]
    upcoming_section = result.split("## Due within 14 days", 1)[1].split(
        "## Submitted, awaiting grade", 1
    )[0]
    awaiting_section = result.split("## Submitted, awaiting grade", 1)[1].split(
        "## Graded", 1
    )[0]
    assert "Missing work" in missing_section
    assert "Upcoming work" in upcoming_section
    assert "Waiting for grade" in awaiting_section
