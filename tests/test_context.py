from __future__ import annotations

from datetime import datetime, timezone

import pytest

from canvascontext.commands import _parser
from canvascontext.config import load_config
from canvascontext.mirror import build_root_index
from canvascontext.render import html_to_markdown
from canvascontext.status import generate_status
from canvascontext.storage import stable_canvas_data, write_text_if_changed
from canvascontext.transcripts import add_transcript


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


def test_transcript_add_help_describes_inputs(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["transcript", "add", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Store a lecture transcript" in output
    assert "Course slug, such as math-300" in output
    assert "Lecture date" in output
    assert "standard input" in output


def test_transcript_add_requires_date(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["transcript", "add", "math-300"])

    assert exc_info.value.code == 2
    assert "the following arguments are required: --date" in capsys.readouterr().err


def test_transcript_add_rejects_invalid_date(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(
            ["transcript", "add", "math-300", "--date", "2026-02-30"]
        )

    assert exc_info.value.code == 2
    assert "expected YYYY-MM-DD" in capsys.readouterr().err


def test_offline_config_does_not_require_token_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    config_path = tmp_path / "canvascontext.toml"
    config_path.write_text(
        'token_file = "missing.json"\n'
        'output = "courses"\n'
        '[[courses]]\n'
        'id = 420\n'
        'slug = "math-420"\n'
    )

    config = load_config(config_path, require_token=False)

    assert config.token == ""
    with pytest.raises(SystemExit):
        load_config(config_path, require_token=True)


def test_root_index_keeps_all_provided_courses_and_agent_instructions(tmp_path) -> None:
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
    assert "authoritative structured source" in (tmp_path / "AGENTS.md").read_text()


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
