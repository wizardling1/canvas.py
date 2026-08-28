from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from canvasapi import CanvasClient, CanvasError

from .config import CourseSpec, MirrorConfig, load_config
from .mirror import CourseMirror, build_root_index
from .status import generate_status
from .storage import read_json, write_text_if_changed
from .transcripts import add_transcript


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Show the relevant command help whenever argument parsing fails."""

    def error(self, message: str) -> None:
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


def _iso_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        )
    return value


def _add_sync_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "sync",
        help="Synchronize Canvas course content",
        description=(
            "Synchronize configured courses from Canvas. If no course slugs are "
            "provided, all configured courses are synchronized."
        ),
    )
    parser.add_argument(
        "courses",
        nargs="*",
        metavar="COURSE",
        help="Course slug, such as math-300; default: all configured courses",
    )
    parser.set_defaults(handler=_handle_sync, requires_token=True)


def _add_status_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "status",
        help="Regenerate course status summaries",
        description=(
            "Regenerate STATUS.md from the existing local mirror without "
            "contacting Canvas."
        ),
    )
    parser.add_argument(
        "courses",
        nargs="*",
        metavar="COURSE",
        help="Course slug, such as math-300; default: all configured courses",
    )
    parser.set_defaults(handler=_handle_status, requires_token=False)


def _add_transcript_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "transcript",
        help="Manage user-provided lecture transcripts",
        description="Store user-provided lecture transcripts in course mirrors.",
    )
    transcript_subparsers = parser.add_subparsers(
        dest="transcript_command",
        required=True,
        metavar="COMMAND",
        title="transcript commands",
    )
    add = transcript_subparsers.add_parser(
        "add",
        help="Store a lecture transcript",
        description=(
            "Store a lecture transcript under the selected course and date. "
            "Text is read from standard input unless --file is provided."
        ),
        epilog=(
            "example:\n"
            "  canvasmirror transcript add math-300 --date 2026-08-28 "
            "< transcript.txt"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add.add_argument(
        "course", metavar="COURSE", help="Course slug, such as math-300"
    )
    add.add_argument(
        "--date",
        required=True,
        type=_iso_date,
        metavar="YYYY-MM-DD",
        help="Lecture date; also used as the document title and filename",
    )
    add.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help="Read transcript text from this file instead of standard input",
    )
    add.set_defaults(handler=_add_transcript, requires_token=False)


def _add_verify_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "verify",
        help="Check mirror completeness",
        description=(
            "Check that each local course mirror contains its required generated "
            "files. This command does not contact Canvas."
        ),
    )
    parser.add_argument(
        "courses",
        nargs="*",
        metavar="COURSE",
        help="Course slug, such as math-300; default: all configured courses",
    )
    parser.set_defaults(handler=_handle_verify, requires_token=False)


def _parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        prog="canvasmirror",
        description="Maintain local, agent-readable Canvas course mirrors.",
        epilog=(
            "examples:\n"
            "  canvasmirror sync\n"
            "  canvasmirror sync math-300\n"
            "  canvasmirror status math-300\n"
            "  canvasmirror transcript add math-300 --date 2026-08-28\n"
            "  canvasmirror verify"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("canvasmirror.toml"),
        metavar="PATH",
        help="Configuration file (default: canvasmirror.toml)",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND", title="commands"
    )
    _add_sync_parser(subparsers)
    _add_status_parser(subparsers)
    _add_transcript_parser(subparsers)
    _add_verify_parser(subparsers)
    return parser


def _select_courses(config: MirrorConfig, names: list[str]) -> list[CourseSpec]:
    if not names:
        return list(config.courses)
    requested = set(names)
    selected = [course for course in config.courses if course.slug in requested]
    missing = requested - {course.slug for course in selected}
    if missing:
        raise SystemExit(f"Unknown course slug(s): {', '.join(sorted(missing))}")
    return selected


def _sync(config: MirrorConfig, names: list[str]) -> int:
    config.output.mkdir(parents=True, exist_ok=True)
    client = CanvasClient(base_url=config.base_url, token=config.token)
    results: list[dict[str, Any]] = []
    failures = 0
    for course in _select_courses(config, names):
        print(f"Syncing {course.slug} (Canvas {course.id})...")
        mirror = CourseMirror(
            client=client,
            course=course,
            output_root=config.output,
            max_file_bytes=config.max_file_bytes,
        )
        try:
            result = mirror.sync()
        except CanvasError as exc:
            failures += 1
            print(f"  Failed: {exc}", file=sys.stderr)
            continue
        results.append(result)
        counts = result["counts"]
        print(
            f"  Done: {counts['modules']} modules, "
            f"{counts['assignments']} assignments, {counts['files']} files; "
            f"{result['changed_files']} local changes"
        )
        for warning in result["warnings"]:
            print(f"  Warning: {warning}")
    if results:
        all_results = []
        for course in config.courses:
            result = read_json(
                config.output / course.slug / ".canvas" / "last-sync.json", {}
            )
            if result:
                all_results.append(result)
        build_root_index(config.output, all_results)
    return 1 if failures else 0


def _regenerate_status(config: MirrorConfig, names: list[str]) -> int:
    failures = 0
    for course in _select_courses(config, names):
        root = config.output / course.slug
        raw = root / ".canvas" / "raw"
        last_sync = read_json(root / ".canvas" / "last-sync.json", {})
        course_data = read_json(raw / "course.json")
        if not course_data:
            failures += 1
            print(f"{course.slug}: no mirror; run sync first", file=sys.stderr)
            continue
        status = generate_status(
            course=course_data,
            assignments=read_json(raw / "assignments.json", []),
            submissions=read_json(raw / "submissions.json", []),
            modules=read_json(raw / "modules.json", []),
            announcements=read_json(raw / "announcements.json", []),
            synced_at=last_sync.get("synced_at") or "unknown",
        )
        write_text_if_changed(root / "STATUS.md", status)
        print(f"Regenerated {root / 'STATUS.md'}")
    return 1 if failures else 0


def _add_transcript(config: MirrorConfig, args: argparse.Namespace) -> int:
    course = _select_courses(config, [args.course])[0]
    text = args.file.read_text() if args.file else sys.stdin.read()
    if not text.strip():
        raise SystemExit("Transcript text is empty")
    destination = add_transcript(
        course_root=config.output / course.slug,
        course_id=course.id,
        date=args.date,
        text=text,
    )
    print(destination)
    return 0


def _verify(config: MirrorConfig, names: list[str]) -> int:
    failures = 0
    required = ["INDEX.md", "COURSE.md", "STATUS.md", "SYLLABUS.md"]
    for course in _select_courses(config, names):
        root = config.output / course.slug
        missing = [name for name in required if not (root / name).exists()]
        if not (root / ".canvas" / "manifest.sqlite").exists():
            missing.append(".canvas/manifest.sqlite")
        if not (root / ".canvas" / "last-sync.json").exists():
            missing.append(".canvas/last-sync.json")
        if missing:
            failures += 1
            print(f"{course.slug}: missing {', '.join(missing)}")
        else:
            result = read_json(root / ".canvas" / "last-sync.json", {})
            print(
                f"{course.slug}: OK; last sync {result.get('synced_at')}; "
                f"warnings={len(result.get('warnings') or [])}"
            )
    return 1 if failures else 0


def _handle_sync(config: MirrorConfig, args: argparse.Namespace) -> int:
    return _sync(config, args.courses)


def _handle_status(config: MirrorConfig, args: argparse.Namespace) -> int:
    return _regenerate_status(config, args.courses)


def _handle_verify(config: MirrorConfig, args: argparse.Namespace) -> int:
    return _verify(config, args.courses)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    config = load_config(args.config, require_token=args.requires_token)
    code = args.handler(config, args)
    if code:
        raise SystemExit(code)
