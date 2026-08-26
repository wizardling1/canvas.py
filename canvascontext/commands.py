from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from canvasapi import CanvasClient, CanvasError

from .config import ContextConfig, CourseSpec, load_config
from .mirror import CourseMirror, build_root_index
from .status import generate_status
from .storage import read_json, write_text_if_changed
from .transcripts import add_transcript


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canvascontext", description="Maintain local Canvas course mirrors"
    )
    parser.add_argument(
        "--config", type=Path, default=Path("canvascontext.toml")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Synchronize Canvas course content")
    sync.add_argument("courses", nargs="*", help="Course slugs; default is all")

    status = subparsers.add_parser(
        "status", help="Regenerate STATUS.md from the last mirror"
    )
    status.add_argument("courses", nargs="*", help="Course slugs; default is all")

    transcript = subparsers.add_parser(
        "transcript", help="Add a user-provided lecture transcript"
    )
    transcript_subparsers = transcript.add_subparsers(
        dest="transcript_command", required=True
    )
    add = transcript_subparsers.add_parser("add")
    add.add_argument("course")
    add.add_argument("--date", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--file", type=Path)

    verify = subparsers.add_parser("verify", help="Check mirror completeness")
    verify.add_argument("courses", nargs="*", help="Course slugs; default is all")
    return parser


def _select_courses(config: ContextConfig, names: list[str]) -> list[CourseSpec]:
    if not names:
        return list(config.courses)
    requested = set(names)
    selected = [course for course in config.courses if course.slug in requested]
    missing = requested - {course.slug for course in selected}
    if missing:
        raise SystemExit(f"Unknown course slug(s): {', '.join(sorted(missing))}")
    return selected


def _sync(config: ContextConfig, names: list[str]) -> int:
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


def _regenerate_status(config: ContextConfig, names: list[str]) -> int:
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


def _add_transcript(config: ContextConfig, args: argparse.Namespace) -> int:
    course = _select_courses(config, [args.course])[0]
    text = args.file.read_text() if args.file else sys.stdin.read()
    if not text.strip():
        raise SystemExit("Transcript text is empty")
    destination = add_transcript(
        course_root=config.output / course.slug,
        course_id=course.id,
        date=args.date,
        title=args.title,
        text=text,
    )
    print(destination)
    return 0


def _verify(config: ContextConfig, names: list[str]) -> int:
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


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    config = load_config(args.config, require_token=args.command == "sync")
    if args.command == "sync":
        code = _sync(config, args.courses)
    elif args.command == "status":
        code = _regenerate_status(config, args.courses)
    elif args.command == "transcript" and args.transcript_command == "add":
        code = _add_transcript(config, args)
    elif args.command == "verify":
        code = _verify(config, args.courses)
    else:
        raise SystemExit("Unknown command")
    if code:
        raise SystemExit(code)
