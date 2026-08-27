from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import fetch_assignments
import fetch_pdfs
import list_assignments
import list_pdfs
import pick_class
import zoom
from canvascli.commands import _parser as canvas_parser
from canvascontext.commands import _parser as context_parser


def test_canvas_without_command_shows_description_and_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        canvas_parser().parse_args([])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "Inspect a Canvas course" in error
    assert "status" in error
    assert "pick" in error
    assert "fetch" in error


def test_canvas_fetch_parser_rejects_extra_arguments(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        canvas_parser().parse_args(["fetch", "first", "second"])

    assert exc_info.value.code == 2
    assert "unrecognized arguments: second" in capsys.readouterr().err


def test_canvas_fetch_parser_returns_typed_output_path() -> None:
    args = canvas_parser().parse_args(["fetch", "course-files"])

    assert args.output == Path("course-files")


def test_context_without_command_shows_description_and_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        context_parser().parse_args([])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "Maintain local, agent-readable Canvas course mirrors" in error
    assert "sync" in error
    assert "transcript" in error
    assert "verify" in error


def test_context_transcript_without_subcommand_shows_relevant_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        context_parser().parse_args(["transcript"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "Store user-provided lecture transcripts" in error
    assert "add" in error


@pytest.mark.parametrize(
    ("parser_factory", "description"),
    [
        (fetch_pdfs._parser, "Download PDF files referenced by visible Canvas modules"),
        (
            fetch_assignments._parser,
            "Download attachments and Canvas file links found in visible assignments",
        ),
        (list_assignments._parser, "List assignments and your submission state"),
        (list_pdfs._parser, "List PDF files visible through Canvas modules"),
        (pick_class._parser, "List Canvas courses and save the selected course_id"),
        (zoom._parser, "Open Zoom cloud recordings"),
    ],
)
def test_legacy_tool_help_is_available_without_running_tool(
    parser_factory: Callable[[], object], description: str, capsys
) -> None:
    parser = parser_factory()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])

    assert exc_info.value.code == 0
    assert description in capsys.readouterr().out
