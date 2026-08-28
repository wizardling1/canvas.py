from __future__ import annotations

from pathlib import Path

import pytest

from canvascli.commands import _parser as canvas_parser
from canvascli.config import load_config_from_cwd, save_config
from canvasmirror.commands import _parser as mirror_parser


def test_canvas_without_command_shows_description_and_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        canvas_parser().parse_args([])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "usage: canvascli" in error
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


def test_canvas_config_uses_only_environment_token(tmp_path, monkeypatch) -> None:
    (tmp_path / "canvascli.toml").write_text(
        'base_url = "https://canvas.example"\n'
        'course_id = 42\n'
        'token = "file-secret"\n'
    )
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)

    config = load_config_from_cwd(tmp_path)

    assert config == {"base_url": "https://canvas.example", "course_id": 42}
    monkeypatch.setenv("CANVAS_TOKEN", "environment-secret")
    assert load_config_from_cwd(tmp_path)["token"] == "environment-secret"


def test_save_config_never_persists_token(tmp_path) -> None:
    path = save_config(
        {
            "base_url": "https://canvas.example",
            "course_id": 42,
            "token": "environment-secret",
        },
        tmp_path,
    )

    assert path.name == "canvascli.toml"
    assert path.read_text() == (
        'base_url = "https://canvas.example"\n'
        'course_id = 42\n'
    )


def test_mirror_without_command_shows_description_and_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        mirror_parser().parse_args([])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "Maintain local, agent-readable Canvas course mirrors" in error
    assert "sync" in error
    assert "transcript" in error
    assert "verify" in error


def test_mirror_transcript_without_subcommand_shows_relevant_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        mirror_parser().parse_args(["transcript"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "Store user-provided lecture transcripts" in error
    assert "add" in error
