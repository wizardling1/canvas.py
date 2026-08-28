from __future__ import annotations

import builtins
import json
from argparse import Namespace
from pathlib import Path

import pytest

from canvasapi import CanvasAuthenticationError, CanvasResponseError
from canvascli import commands
from canvascli.commands import _parser, main
from canvascli.diagnostics import CLIError
from canvascli.repository import (
    DEFAULT_MAX_FILE_BYTES,
    CanvasRepository,
    CourseRecord,
    discover_repository,
    initialize_repository,
    load_repository,
    normalize_base_url,
    save_repository,
)


def test_without_command_shows_description_and_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args([])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "usage: canvascli" in error
    assert "maintain a local course mirror" in error
    assert "init" in error
    assert "list-courses" in error
    assert "add-course" in error
    assert "sync" in error


def test_parser_uses_explicit_course_ids_and_files() -> None:
    assert _parser().parse_args(["ls", "42"]).course_id == 42
    fetch = _parser().parse_args(["fetch", "42", "downloads"])
    assert fetch.course_id == 42
    assert fetch.output == Path("downloads")
    transcript = _parser().parse_args(
        ["transcript", "add", "42", "--date", "2026-08-28", "--file", "note.txt"]
    )
    assert transcript.course_id == 42
    assert transcript.file == Path("note.txt")
    assert "pick" not in _parser()._subparsers._group_actions[0].choices


def test_transcript_requires_file(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(
            ["transcript", "add", "42", "--date", "2026-08-28"]
        )
    assert exc_info.value.code == 2
    assert "--file" in capsys.readouterr().err


def test_course_ids_must_be_positive(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["add-course", "-1"])
    assert exc_info.value.code == 2
    assert "course ID must be a positive integer" in capsys.readouterr().err


def test_init_generates_complete_repository_without_token(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    repository = initialize_repository(tmp_path, "https://canvas.example.edu/")

    assert repository.path == tmp_path / "canvasmirror.json"
    data = json.loads(repository.path.read_text())
    assert data == {
        "version": 1,
        "canvas": {"base_url": "https://canvas.example.edu"},
        "settings": {"max_file_bytes": DEFAULT_MAX_FILE_BYTES},
        "courses": [],
    }


def test_repository_discovery_walks_parents(tmp_path) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    nested = tmp_path / "one" / "two"
    nested.mkdir(parents=True)

    assert discover_repository(nested) == repository


def test_init_refuses_nested_repository(tmp_path) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    nested = tmp_path / "nested"
    nested.mkdir()

    with pytest.raises(CLIError, match="already inside Canvas repository"):
        initialize_repository(nested, "https://other.example.edu")


def test_invalid_json_reports_line_and_column(tmp_path) -> None:
    path = tmp_path / "canvasmirror.json"
    path.write_text('{\n  "version": 1,\n  nope\n')

    with pytest.raises(CLIError, match=r"line 3, column 3"):
        load_repository(path)


def test_repository_rejects_unsupported_schema_and_duplicates(tmp_path) -> None:
    path = tmp_path / "canvasmirror.json"
    path.write_text('{"version": 9}')
    with pytest.raises(CLIError, match="unsupported repository schema"):
        load_repository(path)

    data = {
        "version": 1,
        "canvas": {"base_url": "https://canvas.example.edu"},
        "settings": {"max_file_bytes": 10},
        "courses": [
            {"id": 7, "slug": "one", "name": "One"},
            {"id": 7, "slug": "two", "name": "Two"},
        ],
    }
    path.write_text(json.dumps(data))
    with pytest.raises(CLIError, match="duplicate course ID"):
        load_repository(path)


def test_base_url_rejects_api_paths_and_credentials() -> None:
    with pytest.raises(CLIError, match="must not include /api/v1"):
        normalize_base_url("https://canvas.example.edu/api/v1")
    with pytest.raises(CLIError, match="must not contain credentials"):
        normalize_base_url("https://user:secret@canvas.example.edu")
    with pytest.raises(CLIError, match="without a path"):
        normalize_base_url("https://canvas.example.edu/courses")


def test_missing_repository_has_actionable_error(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        main(["status"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "no canvasmirror.json found" in error
    assert "canvascli init --base-url URL" in error


def test_remote_command_reports_missing_token(tmp_path, monkeypatch, capsys) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main(["list-courses"])

    assert exc_info.value.code == 3
    assert "CANVAS_TOKEN is not set" in capsys.readouterr().err


def test_authentication_error_is_actionable(tmp_path, monkeypatch, capsys) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CANVAS_TOKEN", "expired")
    monkeypatch.setattr(
        commands,
        "available_courses",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CanvasAuthenticationError("rejected")
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main(["list-courses"])

    assert exc_info.value.code == 3
    error = capsys.readouterr().err
    assert "Canvas rejected CANVAS_TOKEN" in error
    assert "invalid, expired" in error


def _canvas_course(course_id: int, name: str, code: str) -> dict[str, object]:
    return {
        "id": course_id,
        "name": name,
        "course_code": code,
        "workflow_state": "available",
        "term": {"name": "Fall 2026"},
    }


def test_add_course_validates_then_writes_once(tmp_path, monkeypatch) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "_client", lambda _repository: object())
    monkeypatch.setattr(
        commands,
        "validate_course_ids",
        lambda _client, ids: [
            _canvas_course(course_id, f"Course {course_id}", f"C {course_id}")
            for course_id in ids
        ],
    )

    assert commands.cmd_add_course(Namespace(course_ids=[7, 8], all=False, all_active=False)) == 0

    repository = discover_repository()
    assert [course.id for course in repository.courses] == [7, 8]
    assert len({course.slug for course in repository.courses}) == 2
    assert not (tmp_path / ".canvasmirror.json.tmp").exists()


@pytest.mark.parametrize(
    ("all_courses", "all_active", "include_all"),
    [(True, False, True), (False, True, False)],
)
def test_bulk_add_scope_is_explicit(
    tmp_path, monkeypatch, all_courses, all_active, include_all
) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "_client", lambda _repository: object())
    observed: list[bool] = []

    def available(_client: object, *, include_all: bool) -> list[dict[str, object]]:
        observed.append(include_all)
        return [_canvas_course(7, "Course Seven", "C 7")]

    monkeypatch.setattr(commands, "available_courses", available)

    assert commands.cmd_add_course(
        Namespace(course_ids=[], all=all_courses, all_active=all_active)
    ) == 0
    assert observed == [include_all]


def test_add_course_failure_does_not_modify_repository(tmp_path, monkeypatch) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    before = repository.path.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "_client", lambda _repository: object())

    def reject(_client: object, _ids: object) -> list[dict[str, object]]:
        raise CLIError("course 99 is inaccessible")

    monkeypatch.setattr(commands, "validate_course_ids", reject)
    with pytest.raises(CLIError, match="inaccessible"):
        commands.cmd_add_course(
            Namespace(course_ids=[7, 99], all=False, all_active=False)
        )
    assert repository.path.read_bytes() == before


def test_add_course_rejects_ambiguous_or_duplicate_selection(tmp_path, monkeypatch) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "_client", lambda _repository: object())

    with pytest.raises(CLIError, match="cannot be combined"):
        commands.cmd_add_course(
            Namespace(course_ids=[7], all=True, all_active=False)
        )
    with pytest.raises(CLIError, match="duplicate course IDs"):
        commands.cmd_add_course(
            Namespace(course_ids=[7, 7], all=False, all_active=False)
        )
    with pytest.raises(CLIError, match="requires one or more course IDs"):
        commands.cmd_add_course(
            Namespace(course_ids=[], all=False, all_active=False)
        )


def test_remove_course_preserves_mirror_files(tmp_path, monkeypatch, capsys) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    repository = repository.with_courses(
        [CourseRecord(id=7, slug="course-seven", name="Course Seven")]
    )
    save_repository(repository)
    mirror_file = tmp_path / "course-seven" / "transcripts" / "lecture.md"
    mirror_file.parent.mkdir(parents=True)
    mirror_file.write_text("user data")
    monkeypatch.chdir(tmp_path)

    assert commands.cmd_remove_course(Namespace(course_ids=[7])) == 0

    assert discover_repository().courses == ()
    assert mirror_file.read_text() == "user data"
    assert "retained existing mirror files" in capsys.readouterr().err


def test_remove_course_is_atomic_when_any_id_is_unknown(tmp_path, monkeypatch) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    repository = repository.with_courses(
        [CourseRecord(id=7, slug="course-seven", name="Course Seven")]
    )
    save_repository(repository)
    before = repository.path.read_bytes()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CLIError, match="not tracked"):
        commands.cmd_remove_course(Namespace(course_ids=[7, 99]))
    assert repository.path.read_bytes() == before


def test_status_reports_broken_local_mirror(tmp_path, monkeypatch, capsys) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    repository = repository.with_courses(
        [CourseRecord(id=7, slug="course-seven", name="Course Seven")]
    )
    save_repository(repository)
    (tmp_path / "course-seven").mkdir()
    monkeypatch.chdir(tmp_path)

    assert commands.cmd_status(Namespace()) == 1
    captured = capsys.readouterr()
    assert "MISSING:" in captured.out
    assert "canvascli: warning: course 7" in captured.err


def test_noninteractive_local_commands_never_read_input(tmp_path, monkeypatch) -> None:
    initialize_repository(tmp_path, "https://canvas.example.edu")
    monkeypatch.chdir(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("interactive input was attempted")

    monkeypatch.setattr(builtins, "input", forbidden)
    assert commands.cmd_status(Namespace()) == 0


def test_transcript_reads_only_explicit_file(tmp_path, monkeypatch) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    repository = repository.with_courses(
        [CourseRecord(id=7, slug="course-seven", name="Course Seven")]
    )
    save_repository(repository)
    source = tmp_path / "lecture.txt"
    source.write_text("Lecture notes")
    monkeypatch.chdir(tmp_path)

    result = commands.cmd_transcript_add(
        Namespace(course_id=7, date="2026-08-28", file=source)
    )

    assert result == 0
    assert "Lecture notes" in (
        tmp_path / "course-seven" / "transcripts" / "2026-08-28.md"
    ).read_text()


def test_sync_continues_after_course_failure(tmp_path, monkeypatch, capsys) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    repository = repository.with_courses(
        [
            CourseRecord(id=7, slug="seven", name="Seven"),
            CourseRecord(id=8, slug="eight", name="Eight"),
        ]
    )
    save_repository(repository)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "_client", lambda _repository: object())
    attempted: list[int] = []

    class FakeMirror:
        def __init__(self, **kwargs: object) -> None:
            self.course = kwargs["course"]

        def sync(self) -> dict[str, object]:
            attempted.append(self.course.id)
            if self.course.id == 7:
                raise CanvasResponseError("temporary Canvas failure")
            return {
                "counts": {"modules": 1, "assignments": 2, "files": 3},
                "changed_files": 4,
                "warnings": [],
            }

    monkeypatch.setattr(commands, "CourseMirror", FakeMirror)

    assert commands.cmd_sync(Namespace(course_ids=[])) == 1
    assert attempted == [7, 8]
    assert "1 course synchronization(s) failed" in capsys.readouterr().err


def test_sync_aborts_immediately_for_invalid_token(tmp_path, monkeypatch) -> None:
    repository = initialize_repository(tmp_path, "https://canvas.example.edu")
    repository = repository.with_courses(
        [
            CourseRecord(id=7, slug="seven", name="Seven"),
            CourseRecord(id=8, slug="eight", name="Eight"),
        ]
    )
    save_repository(repository)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands, "_client", lambda _repository: object())
    attempted: list[int] = []

    class RejectingMirror:
        def __init__(self, **kwargs: object) -> None:
            self.course = kwargs["course"]

        def sync(self) -> dict[str, object]:
            attempted.append(self.course.id)
            raise CanvasAuthenticationError("rejected")

    monkeypatch.setattr(commands, "CourseMirror", RejectingMirror)

    with pytest.raises(CanvasAuthenticationError):
        commands.cmd_sync(Namespace(course_ids=[]))
    assert attempted == [7]
