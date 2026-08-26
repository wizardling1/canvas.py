from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from tabulate import tabulate

from canvasapi import (
    DEFAULT_BASE_URL,
    CanvasAuthorizationError,
    CanvasClient,
    CanvasError,
    CanvasNotFoundError,
)
from canvasapi.assignments import list_assignments
from canvasapi.courses import get_course, list_courses
from canvasapi.files import get_file, list_course_files
from canvasapi.modules import list_modules

from .config import CanvasConfig, load_config_from_cwd, save_config
from .downloads import assignment_file_ids, conditional_download, file_destination
from .formatting import human_size, iso_to_local
from .utils import normalize_course_stem, safe_filename


def client_from_config(config: CanvasConfig) -> CanvasClient:
    token = config.get("token")
    if not token:
        raise SystemExit("canvas_config.json or CANVAS_TOKEN must provide 'token'")
    return CanvasClient(
        base_url=str(config.get("base_url") or DEFAULT_BASE_URL), token=str(token)
    )


def selected_course_id(config: CanvasConfig) -> int:
    course_id = config.get("course_id")
    if not course_id:
        raise SystemExit("canvas_config.json must include 'course_id'")
    return int(course_id)


def cmd_status() -> None:
    config = load_config_from_cwd()
    course_id = selected_course_id(config)
    course = get_course(client_from_config(config), course_id)
    name = course.get("name") or course.get("course_code") or "(unnamed course)"
    print("Canvas CLI")
    print(f"- Current course: id={course_id} — {name}")
    print("\nAvailable commands:")
    print("  pick        Select or set the active course")
    print("  ls          List PDFs and assignments")
    print("  fetch       Download PDFs and assignment files")


def _course_sort_key(course: dict[str, Any]) -> tuple[str, str]:
    term = course.get("term") or {}
    return term.get("start_at") or "9999", course.get("name") or ""


def cmd_pick(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="canvas.py pick")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--search")
    parser.add_argument("--set-id", type=int)
    args = parser.parse_args(argv)

    config = load_config_from_cwd()
    client = client_from_config(config)
    if args.set_id:
        config["course_id"] = args.set_id
        path = save_config(config)
        print(f"Saved course_id={args.set_id} to {path}")
        return

    courses = list_courses(
        client, enrollment_state=None if args.all else "active"
    )
    if args.search:
        needle = args.search.casefold()
        courses = [
            course
            for course in courses
            if needle in str(course.get("name") or "").casefold()
            or needle in str(course.get("course_code") or "").casefold()
        ]
    courses.sort(key=_course_sort_key)
    if not courses:
        print("No courses found for the chosen filters.")
        return

    rows = [
        [
            index,
            course.get("id"),
            (course.get("term") or {}).get("name") or "",
            course.get("course_code") or "",
            course.get("name") or "",
        ]
        for index, course in enumerate(courses, 1)
    ]
    print(tabulate(rows, headers=["#", "id", "term", "code", "name"], tablefmt="github"))
    while True:
        choice = input(f"Enter a number 1-{len(courses)} (or 'q' to quit): ").strip()
        if choice.casefold() in {"q", "quit", "exit"}:
            print("Aborted. Nothing saved.")
            return
        if choice.isdigit() and 1 <= int(choice) <= len(courses):
            course = courses[int(choice) - 1]
            config["course_id"] = int(course["id"])
            path = save_config(config)
            print(f"Saved course_id={course['id']} to {path}")
            return
        print("Invalid selection. Please try again.")


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


def collect_pdfs(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
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


def visible_assignments(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    return [
        assignment
        for assignment in list_assignments(client, course_id)
        if assignment.get("published", True)
        and not assignment.get("locked_for_user", False)
    ]


def cmd_ls() -> None:
    config = load_config_from_cwd()
    course_id = selected_course_id(config)
    client = client_from_config(config)
    _ = get_course(client, course_id)

    print("PDFs\n====")
    pdfs = collect_pdfs(client, course_id)
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
        print(f"Found {len(pdfs)} PDF(s):\n")
        print(tabulate(rows, headers=["ID", "Name", "Size", "Updated", "Src"], tablefmt="github"))

    print("\nAssignments\n===========")
    assignments = visible_assignments(client, course_id)
    if not assignments:
        print("No assignments found.")
        return
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
                assignment.get("published", False),
            ]
        )
    print(
        tabulate(
            rows,
            headers=["name", "due", "points", "score", "workflow_state", "late", "missing", "published"],
            tablefmt="github",
            disable_numparse=True,
        )
    )


def cmd_fetch(argv: list[str]) -> None:
    config = load_config_from_cwd()
    course_id = selected_course_id(config)
    client = client_from_config(config)
    course = get_course(client, course_id)
    if argv:
        output = Path(argv[0]).expanduser().resolve()
    else:
        name = course.get("name") or course.get("course_code") or "course"
        output = Path.cwd() / f"{normalize_course_stem(str(name))}_downloads"
    if output.exists():
        print(f"Output directory exists: {output}")
    else:
        print(f"Creating output directory: {output}")
        output.mkdir(parents=True, exist_ok=True)
    print(f"Saving to: {output}")

    print("Fetching PDFs...")
    pdfs = collect_pdfs(client, course_id)
    used_names: dict[str, int] = {}
    changed = 0
    for metadata in pdfs:
        destination = file_destination(output, metadata, used_names)
        changed += int(conditional_download(client, metadata, destination))
        time.sleep(0.1)
    print(f"PDFs found: {len(pdfs)}; changed: {changed}")

    print("\nFetching assignment attachments and linked files...")
    assignments_seen = 0
    assignment_files_seen = 0
    assignment_files_changed = 0
    for assignment in list_assignments(client, course_id):
        assignments_seen += 1
        file_ids = assignment_file_ids(assignment)
        if not file_ids:
            continue
        assignment_name = safe_filename(
            str(assignment.get("name") or f"assignment_{assignment.get('id')}")
        )
        directory = output / (assignment_name or "assignment")
        for file_id in sorted(file_ids):
            try:
                metadata = get_file(client, file_id)
            except (CanvasAuthorizationError, CanvasNotFoundError) as exc:
                print(f"Skipping file {file_id}: {exc}")
                continue
            assignment_files_seen += 1
            destination = file_destination(directory, metadata)
            assignment_files_changed += int(
                conditional_download(client, metadata, destination)
            )
    if assignments_seen == 0:
        print("No assignments visible to this token/course.")
    elif assignment_files_seen == 0:
        print("No downloadable files found in assignment attachments or descriptions.")
    else:
        print(
            f"Assignment files found: {assignment_files_seen}; "
            f"changed: {assignment_files_changed}"
        )
    print("\nDone.")


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if not arguments:
            cmd_status()
            return
        command, rest = arguments[0], arguments[1:]
        if command == "pick":
            cmd_pick(rest)
        elif command in {"ls", "list"}:
            cmd_ls()
        elif command == "fetch":
            cmd_fetch(rest)
        else:
            raise SystemExit(
                f"Unknown command: {command}\nUsage: canvas.py [pick|ls|fetch [OUTDIR]]"
            )
    except CanvasError as exc:
        raise SystemExit(str(exc)) from exc
