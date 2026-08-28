from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import requests
from tabulate import tabulate

from canvasapi import (
    CanvasAuthenticationError,
    CanvasAuthorizationError,
    CanvasClient,
    CanvasError,
    CanvasNotFoundError,
    CanvasResponseError,
)
from canvasapi.assignments import list_assignments
from canvasapi.courses import get_course
from canvasapi.files import get_file, list_course_files
from canvasapi.modules import list_modules

from .courses import available_courses, records_for_courses, validate_course_ids
from .diagnostics import CLIError, render_error
from .downloads import assignment_file_ids, conditional_download, file_destination
from .formatting import human_size, iso_to_local
from .mirror import CourseMirror, build_root_index
from .mirror.storage import read_json
from .mirror.transcripts import add_transcript
from .parsing import HelpfulArgumentParser
from .repository import (
    CanvasRepository,
    CourseRecord,
    discover_repository,
    initialize_repository,
    save_repository,
)
from .utils import normalize_course_stem, safe_filename


def _warning(message: str) -> None:
    print(f"canvascli: warning: {message}", file=sys.stderr)


def _client(repository: CanvasRepository) -> CanvasClient:
    token = os.getenv("CANVAS_TOKEN")
    if not token:
        raise CLIError(
            "CANVAS_TOKEN is not set",
            "export CANVAS_TOKEN before running commands that contact Canvas",
            exit_code=3,
        )
    return CanvasClient(base_url=repository.base_url, token=token)


def _course_records(
    repository: CanvasRepository, course_ids: Iterable[int]
) -> list[CourseRecord]:
    return [repository.course(course_id) for course_id in course_ids]


def cmd_init(args: argparse.Namespace) -> int:
    repository = initialize_repository(args.directory, args.base_url)
    print(f"Initialized Canvas repository in {repository.root}")
    print(f"Created {repository.path}")
    return 0


def _course_rows(courses: Iterable[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            course.get("id"),
            course.get("workflow_state") or "",
            (course.get("term") or {}).get("name") or "",
            course.get("course_code") or "",
            course.get("name") or "",
        ]
        for course in courses
    ]


def cmd_list_courses(args: argparse.Namespace) -> int:
    repository = discover_repository()
    courses = available_courses(_client(repository), include_all=args.all)
    if not courses:
        print("No courses found.")
        return 0
    print(
        tabulate(
            _course_rows(courses),
            headers=["ID", "STATE", "TERM", "CODE", "NAME"],
            tablefmt="plain",
            disable_numparse=True,
        )
    )
    return 0


def _new_courses(
    repository: CanvasRepository, requested: list[dict[str, Any]]
) -> tuple[list[CourseRecord], list[CourseRecord]]:
    tracked = {course.id: course for course in repository.courses}
    unseen = [course for course in requested if int(course["id"]) not in tracked]
    already = [tracked[int(course["id"])] for course in requested if int(course["id"]) in tracked]
    return records_for_courses(repository, unseen), already


def cmd_add_course(args: argparse.Namespace) -> int:
    repository = discover_repository()
    if args.course_ids and (args.all or args.all_active):
        raise CLIError("course IDs cannot be combined with --all-active or --all")
    if len(args.course_ids) != len(set(args.course_ids)):
        raise CLIError("duplicate course IDs were provided")
    if not args.course_ids and not args.all and not args.all_active:
        raise CLIError(
            "add-course requires one or more course IDs, --all-active, or --all"
        )
    client = _client(repository)
    if args.all or args.all_active:
        requested = available_courses(client, include_all=args.all)
    elif args.course_ids:
        requested = validate_course_ids(client, args.course_ids)
    additions, already = _new_courses(repository, requested)
    for course in already:
        _warning(f"course {course.id} ({course.name}) is already tracked")
    if not additions:
        print("No courses added.")
        return 0

    updated = repository.with_courses((*repository.courses, *additions))
    save_repository(updated)
    for course in additions:
        print(f"Added {course.id}: {course.name} [{course.slug}]")
    print(f"Updated {updated.path}")
    return 0


def cmd_remove_course(args: argparse.Namespace) -> int:
    repository = discover_repository()
    if len(args.course_ids) != len(set(args.course_ids)):
        raise CLIError("duplicate course IDs were provided")
    requested = set(args.course_ids)
    tracked = {course.id: course for course in repository.courses}
    missing = requested - tracked.keys()
    if missing:
        names = ", ".join(str(course_id) for course_id in sorted(missing))
        raise CLIError(f"course ID(s) are not tracked: {names}")

    removed = [tracked[course_id] for course_id in args.course_ids]
    updated = repository.with_courses(
        course for course in repository.courses if course.id not in requested
    )
    save_repository(updated)
    for course in removed:
        print(f"Removed {course.id}: {course.name}")
        directory = repository.root / course.slug
        if directory.exists():
            _warning(f"retained existing mirror files at {directory}")
    return 0


def _select_sync_courses(
    repository: CanvasRepository, course_ids: list[int]
) -> list[CourseRecord]:
    if course_ids:
        if len(course_ids) != len(set(course_ids)):
            raise CLIError("duplicate course IDs were provided")
        return _course_records(repository, course_ids)
    if not repository.courses:
        raise CLIError(
            "this repository tracks no courses",
            "run `canvascli list-courses`, then `canvascli add-course ID`",
        )
    return list(repository.courses)


def _root_results(repository: CanvasRepository) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for course in repository.courses:
        path = repository.root / course.slug / ".canvas" / "last-sync.json"
        try:
            value = read_json(path, {})
        except (json.JSONDecodeError, OSError) as exc:
            _warning(f"cannot read {path}: {exc}")
            continue
        if isinstance(value, dict) and value:
            results.append(value)
    return results


def cmd_sync(args: argparse.Namespace) -> int:
    repository = discover_repository()
    courses = _select_sync_courses(repository, args.course_ids)
    client = _client(repository)
    failures = 0
    for course in courses:
        print(f"Syncing {course.id}: {course.name} [{course.slug}]")
        mirror = CourseMirror(
            client=client,
            course=course,
            output_root=repository.root,
            max_file_bytes=repository.max_file_bytes,
        )
        try:
            result = mirror.sync()
        except CanvasAuthenticationError:
            raise
        except (CanvasError, requests.RequestException, OSError, sqlite3.Error) as exc:
            failures += 1
            print(f"canvascli: error: course {course.id} failed: {exc}", file=sys.stderr)
            continue
        counts = result["counts"]
        print(
            f"Done: {counts['modules']} modules, "
            f"{counts['assignments']} assignments, {counts['files']} files; "
            f"{result['changed_files']} local changes"
        )
        for warning in result["warnings"]:
            _warning(f"course {course.id}: {warning}")

    build_root_index(repository.root, _root_results(repository))
    if failures:
        print(
            f"canvascli: error: {failures} course synchronization(s) failed",
            file=sys.stderr,
        )
        return 1
    return 0


def _manifest_state(path: Path) -> tuple[str, int]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                return "CORRUPT MANIFEST", 0
            row = connection.execute(
                "SELECT COUNT(*) FROM resources WHERE stale=1"
            ).fetchone()
            return "", int(row[0] if row else 0)
        finally:
            connection.close()
    except sqlite3.Error:
        return "CORRUPT MANIFEST", 0


def _course_status(repository: CanvasRepository, course: CourseRecord) -> tuple[list[Any], bool]:
    root = repository.root / course.slug
    internal = root / ".canvas"
    last_sync_path = internal / "last-sync.json"
    manifest_path = internal / "manifest.sqlite"
    if not root.exists():
        return [course.id, course.name, "never", "NOT SYNCHRONIZED"], False

    required = ["INDEX.md", "COURSE.md", "STATUS.md", "SYLLABUS.md"]
    missing = [name for name in required if not (root / name).is_file()]
    if not manifest_path.is_file():
        missing.append(".canvas/manifest.sqlite")
    if not last_sync_path.is_file():
        missing.append(".canvas/last-sync.json")
    if missing:
        return [course.id, course.name, "unknown", f"MISSING: {', '.join(missing)}"], True

    try:
        result = read_json(last_sync_path, {})
    except (json.JSONDecodeError, OSError) as exc:
        return [course.id, course.name, "unknown", f"INVALID LAST SYNC: {exc}"], True
    if not isinstance(result, dict):
        return [course.id, course.name, "unknown", "INVALID LAST SYNC"], True
    manifest_error, stale = _manifest_state(manifest_path)
    if manifest_error:
        return [course.id, course.name, result.get("synced_at") or "unknown", manifest_error], True
    warnings = len(result.get("warnings") or [])
    details = ["OK"]
    if warnings:
        details.append(f"{warnings} warning(s)")
    if stale:
        details.append(f"{stale} stale")
    return [course.id, course.name, result.get("synced_at") or "unknown", "; ".join(details)], False


def cmd_status(_args: argparse.Namespace) -> int:
    repository = discover_repository()
    print(f"Repository: {repository.root}")
    print(f"Canvas:     {repository.base_url}")
    print()
    if not repository.courses:
        _warning("this repository tracks no courses")
        return 0

    rows: list[list[Any]] = []
    broken = False
    never_synced = 0
    for course in repository.courses:
        row, is_broken = _course_status(repository, course)
        rows.append(row)
        broken = broken or is_broken
        never_synced += int(row[3] == "NOT SYNCHRONIZED")
        if is_broken:
            _warning(f"course {course.id}: {row[3]}")
    print(
        tabulate(
            rows,
            headers=["ID", "COURSE", "LAST SYNC", "RESULT"],
            tablefmt="plain",
            disable_numparse=True,
        )
    )

    tracked_slugs = {course.slug for course in repository.courses}
    orphans = sorted(
        path.name
        for path in repository.root.iterdir()
        if path.is_dir()
        and path.name not in tracked_slugs
        and (path / ".canvas").is_dir()
    )
    for slug in orphans:
        _warning(f"untracked mirror directory retained at {repository.root / slug}")
    if never_synced:
        _warning(f"{never_synced} tracked course(s) have never been synchronized")
    return 1 if broken else 0


def _is_pdf(metadata: dict[str, Any]) -> bool:
    name = str(metadata.get("display_name") or metadata.get("filename") or "")
    content_type = str(
        metadata.get("content-type") or metadata.get("content_type") or ""
    )
    return (
        name.casefold().endswith(".pdf")
        or content_type.casefold() == "application/pdf"
        or str(metadata.get("mime_class") or "").casefold() == "pdf"
    )


def collect_pdfs(client: CanvasClient, course_id: int) -> list[dict[str, Any]]:
    files_by_id: dict[int, dict[str, Any]] = {}
    for module in list_modules(client, course_id):
        for item in module.get("items") or []:
            if item.get("type") == "File" and item.get("content_id"):
                file_id = int(item["content_id"])
                try:
                    metadata = get_file(client, file_id)
                except (CanvasAuthorizationError, CanvasNotFoundError):
                    continue
                if _is_pdf(metadata):
                    metadata = dict(metadata)
                    metadata["_source"] = "modules"
                    files_by_id[file_id] = metadata
    try:
        course_files = list_course_files(client, course_id)
    except CanvasAuthorizationError:
        course_files = []
    for metadata in course_files:
        if metadata.get("id") and _is_pdf(metadata):
            metadata = dict(metadata)
            metadata["_source"] = "files"
            files_by_id.setdefault(int(metadata["id"]), metadata)
    return sorted(
        files_by_id.values(),
        key=lambda item: item.get("updated_at") or item.get("modified_at") or "",
        reverse=True,
    )


def visible_assignments(client: CanvasClient, course_id: int) -> list[dict[str, Any]]:
    return [
        assignment
        for assignment in list_assignments(client, course_id)
        if assignment.get("published", True)
        and not assignment.get("locked_for_user", False)
    ]


def cmd_ls(args: argparse.Namespace) -> int:
    repository = discover_repository()
    course = repository.course(args.course_id)
    client = _client(repository)
    get_course(client, course.id)

    print("PDFs\n====")
    pdfs = collect_pdfs(client, course.id)
    if not pdfs:
        print("No PDFs found (or not visible with your account/permissions).")
    else:
        rows = [
            [
                item.get("id"),
                item.get("display_name") or item.get("filename"),
                human_size(item.get("size") or 0),
                iso_to_local(item.get("updated_at") or item.get("modified_at")),
                item.get("_source"),
            ]
            for item in pdfs
        ]
        print(tabulate(rows, headers=["ID", "Name", "Size", "Updated", "Src"], tablefmt="github"))

    print("\nAssignments\n===========")
    assignments = visible_assignments(client, course.id)
    if not assignments:
        print("No assignments found.")
        return 0
    rows = []
    for assignment in assignments:
        submission = assignment.get("submission") or {}
        rows.append(
            [
                assignment.get("name") or "",
                iso_to_local(assignment.get("due_at")),
                assignment.get("points_possible"),
                submission.get("score"),
                submission.get("workflow_state") or "",
                submission.get("late", False),
                submission.get("missing", False),
            ]
        )
    print(
        tabulate(
            rows,
            headers=["name", "due", "points", "score", "state", "late", "missing"],
            tablefmt="github",
            disable_numparse=True,
        )
    )
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    repository = discover_repository()
    course_record = repository.course(args.course_id)
    client = _client(repository)
    course = get_course(client, course_record.id)
    if args.output:
        output = args.output.expanduser().resolve()
    else:
        name = course.get("name") or course.get("course_code") or "course"
        output = Path.cwd() / f"{normalize_course_stem(str(name))}_downloads"
    output.mkdir(parents=True, exist_ok=True)
    print(f"Saving to: {output}")

    pdfs = collect_pdfs(client, course_record.id)
    used_names: dict[str, int] = {}
    changed = 0
    for metadata in pdfs:
        destination = file_destination(output, metadata, used_names)
        changed += int(conditional_download(client, metadata, destination))
        time.sleep(0.1)
    print(f"PDFs found: {len(pdfs)}; changed: {changed}")

    assignment_files_seen = 0
    assignment_files_changed = 0
    for assignment in list_assignments(client, course_record.id):
        file_ids = assignment_file_ids(assignment)
        assignment_name = safe_filename(
            str(assignment.get("name") or f"assignment_{assignment.get('id')}")
        )
        directory = output / (assignment_name or "assignment")
        for file_id in sorted(file_ids):
            try:
                metadata = get_file(client, file_id)
            except (CanvasAuthorizationError, CanvasNotFoundError) as exc:
                _warning(f"skipping file {file_id}: {exc}")
                continue
            assignment_files_seen += 1
            destination = file_destination(directory, metadata)
            assignment_files_changed += int(
                conditional_download(client, metadata, destination)
            )
    print(
        f"Assignment files found: {assignment_files_seen}; "
        f"changed: {assignment_files_changed}"
    )
    return 0


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


def _course_id(value: str) -> int:
    try:
        course_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("course ID must be a positive integer") from exc
    if course_id <= 0:
        raise argparse.ArgumentTypeError("course ID must be a positive integer")
    return course_id


def cmd_transcript_add(args: argparse.Namespace) -> int:
    repository = discover_repository()
    course = repository.course(args.course_id)
    try:
        text = args.file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CLIError(f"cannot read transcript file {args.file}: {exc}", exit_code=5) from exc
    if not text.strip():
        raise CLIError(f"transcript file is empty: {args.file}")
    destination = add_transcript(
        course_root=repository.root / course.slug,
        course_id=course.id,
        date=args.date,
        text=text,
    )
    print(destination)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        prog="canvascli",
        description="Inspect Canvas and maintain a local course mirror repository.",
        epilog=(
            "examples:\n"
            "  canvascli init --base-url https://canvas.example.edu\n"
            "  canvascli list-courses\n"
            "  canvascli add-course 123456\n"
            "  canvascli sync\n"
            "  canvascli status"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND", title="commands"
    )

    init = subparsers.add_parser(
        "init",
        help="Create a Canvas mirror repository",
        description=(
            "Create canvasmirror.json without contacting Canvas. The directory "
            "containing that file becomes the mirror root."
        ),
    )
    init.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("."),
        metavar="DIRECTORY",
        help="Repository directory; default: current directory",
    )
    init.add_argument("--base-url", required=True, metavar="URL", help="Canvas website origin without /api/v1")
    init.set_defaults(handler=cmd_init)

    listing = subparsers.add_parser(
        "list-courses",
        help="List courses visible in Canvas",
        description=(
            "List active courses visible to CANVAS_TOKEN. Use --all to include "
            "past, future, and concluded courses."
        ),
    )
    listing.add_argument("--all", action="store_true", help="Include past, future, and concluded courses")
    listing.set_defaults(handler=cmd_list_courses)

    add = subparsers.add_parser(
        "add-course",
        help="Add Canvas courses to this repository",
        description=(
            "Validate courses with Canvas, generate their local metadata, and "
            "atomically add them to canvasmirror.json."
        ),
    )
    add.add_argument(
        "course_ids", nargs="*", type=_course_id, metavar="ID", help="Canvas course ID"
    )
    scope = add.add_mutually_exclusive_group()
    scope.add_argument("--all-active", action="store_true", help="Add every active course")
    scope.add_argument("--all", action="store_true", help="Add every visible course, including inactive courses")
    add.set_defaults(handler=cmd_add_course)

    remove = subparsers.add_parser(
        "remove-course",
        help="Stop tracking courses without deleting files",
        description=(
            "Atomically remove courses from canvasmirror.json. Existing mirror "
            "files and user transcripts are retained."
        ),
    )
    remove.add_argument(
        "course_ids", nargs="+", type=_course_id, metavar="ID", help="Tracked Canvas course ID"
    )
    remove.set_defaults(handler=cmd_remove_course)

    sync = subparsers.add_parser(
        "sync",
        help="Synchronize tracked Canvas courses",
        description=(
            "Synchronize the specified tracked course IDs. With no IDs, "
            "synchronize every tracked course."
        ),
    )
    sync.add_argument(
        "course_ids", nargs="*", type=_course_id, metavar="ID", help="Tracked Canvas course ID"
    )
    sync.set_defaults(handler=cmd_sync)

    status = subparsers.add_parser(
        "status",
        help="Check local repository and mirror status",
        description=(
            "Inspect repository metadata, generated files, manifests, stale "
            "resources, and retained untracked mirrors without contacting Canvas."
        ),
    )
    status.set_defaults(handler=cmd_status)

    ls = subparsers.add_parser(
        "ls",
        aliases=["list"],
        help="List visible PDFs and assignments for a tracked course",
    )
    ls.add_argument("course_id", type=_course_id, metavar="ID", help="Tracked Canvas course ID")
    ls.set_defaults(handler=cmd_ls)

    fetch = subparsers.add_parser(
        "fetch", help="Download PDFs and assignment files for a tracked course"
    )
    fetch.add_argument("course_id", type=_course_id, metavar="ID", help="Tracked Canvas course ID")
    fetch.add_argument(
        "output", nargs="?", type=Path, metavar="DIRECTORY", help="Download directory"
    )
    fetch.set_defaults(handler=cmd_fetch)

    transcript = subparsers.add_parser("transcript", help="Manage user-provided lecture transcripts")
    transcript_commands = transcript.add_subparsers(dest="transcript_command", required=True, metavar="COMMAND")
    transcript_add = transcript_commands.add_parser(
        "add",
        help="Store a transcript from a file",
        description=(
            "Copy a non-empty UTF-8 text file into a tracked course's transcript "
            "directory with provenance metadata."
        ),
    )
    transcript_add.add_argument("course_id", type=_course_id, metavar="ID", help="Tracked Canvas course ID")
    transcript_add.add_argument(
        "--date", required=True, type=_iso_date, metavar="YYYY-MM-DD", help="Lecture date"
    )
    transcript_add.add_argument(
        "--file", required=True, type=Path, metavar="PATH", help="UTF-8 transcript file"
    )
    transcript_add.set_defaults(handler=cmd_transcript_add)
    return parser


def _canvas_error(error: CanvasError) -> CLIError:
    if isinstance(error, CanvasAuthenticationError):
        return CLIError(
            "Canvas rejected CANVAS_TOKEN",
            "the token may be invalid, expired, or issued by another Canvas installation",
            exit_code=3,
        )
    if isinstance(error, CanvasAuthorizationError):
        return CLIError(
            "Canvas denied access to the requested resource",
            "check the account's course enrollment and permissions",
            exit_code=4,
        )
    if isinstance(error, CanvasNotFoundError):
        return CLIError(
            "Canvas resource not found or not visible to this account",
            exit_code=4,
        )
    if isinstance(error, CanvasResponseError):
        return CLIError(str(error), exit_code=4)
    return CLIError(str(error), exit_code=4)


def main(argv: list[str] | None = None) -> None:
    try:
        args = _parser().parse_args(argv)
        code = int(args.handler(args) or 0)
    except CLIError as exc:
        print(render_error(exc), file=sys.stderr)
        raise SystemExit(exc.exit_code) from None
    except CanvasError as exc:
        error = _canvas_error(exc)
        print(render_error(error), file=sys.stderr)
        raise SystemExit(error.exit_code) from None
    except requests.RequestException as exc:
        error = CLIError(f"cannot contact Canvas: {exc}", exit_code=4)
        print(render_error(error), file=sys.stderr)
        raise SystemExit(error.exit_code) from None
    except OSError as exc:
        error = CLIError(f"local filesystem operation failed: {exc}", exit_code=5)
        print(render_error(error), file=sys.stderr)
        raise SystemExit(error.exit_code) from None
    if code:
        raise SystemExit(code)
