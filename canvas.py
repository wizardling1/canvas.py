#!/usr/bin/env python3
"""
canvas.py — unified Canvas CLI entry point (initial scope)

Current commands:
  - (no args): Show selected course (id + name) and available commands
  - pick:      Launch the course picker (interactive) or set by id
  - ls:        List PDFs then assignments for the selected course
  - fetch:     Download PDFs and assignment files into a course-named folder

Relies on a canvas_config.json in the caller's current directory containing at minimum:
  {"token": "<CANVAS_TOKEN>", "course_id": <optional_int>}
"""

from __future__ import annotations

import sys, json
from pathlib import Path
from typing import Optional

import requests

# Local modules
# Lazy-import helpers inside commands to avoid side effects on import

API_BASE = "https://wsu.instructure.com/api/v1"


def load_config_from_cwd_or_die() -> dict:
    cfg_path = Path.cwd() / "canvas_config.json"
    if not cfg_path.exists():
        sys.exit(f"Missing canvas_config.json in {Path.cwd()} — create one with your Canvas token and course_id.")
    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in {cfg_path}: {e}")
    return cfg


def assert_course_access(token: str, course_id: int) -> str:
    """Return course name if accessible; otherwise exit with an error message."""
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})
    try:
        r = sess.get(f"{API_BASE}/courses/{course_id}", timeout=20)
    except requests.RequestException as e:
        sys.exit(f"Failed to reach Canvas API: {e}")
    if r.status_code in (401, 403):
        sys.exit(f"Unauthorized to access course {course_id}. Check your Canvas token and permissions.")
    if r.status_code == 404:
        sys.exit(f"Course {course_id} not found. Check course_id in canvas_config.json.")
    try:
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        sys.exit(f"Unexpected response from Canvas: {e}")
    name = data.get("name") or data.get("course_code") or "(unnamed course)"
    return name


def cmd_status():
    cfg = load_config_from_cwd_or_die()
    token = cfg.get("token")
    cid = cfg.get("course_id")

    print("Canvas CLI")
    if not token or not cid:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    # Validate access and show course
    name = assert_course_access(str(token), int(cid))
    print(f"- Current course: id={cid} — {name}")

    print("\nAvailable commands:")
    print("  pick        Select or set the active course")
    print("  ls          List PDFs and assignments")
    print("  fetch       Download PDFs and assignment files")


def cmd_pick(argv: list[str]):
    # Mirror pick_class flags and delegate
    import argparse
    ap = argparse.ArgumentParser(prog="canvas.py pick",
                                 description="Pick a Canvas course or set by id")
    ap.add_argument("--all", action="store_true", help="Include past/future/ended enrollments")
    ap.add_argument("--search", help="Filter by name or code (case-insensitive)")
    ap.add_argument("--set-id", type=int, help="Non-interactive: set course_id directly and exit")
    args = ap.parse_args(argv)
    import pick_class as pick_class
    pick_class.run(include_all=args.all, search=args.search, set_id=args.set_id)


def cmd_ls():
    cfg = load_config_from_cwd_or_die()
    token = cfg.get("token")
    cid = cfg.get("course_id")
    if not token or not cid:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    # Validate access first
    _ = assert_course_access(str(token), int(cid))

    # PDFs
    print("PDFs\n====")
    import list_pdfs as lp
    pdf_rows = lp.collect_pdfs(sess, str(cid))
    lp.print_pdfs_table(pdf_rows)

    # Assignments
    print("Assignments\n===========")
    import list_assignments as la
    assigns = la.fetch_assignments(sess, str(cid), include_unpublished=False)
    assign_rows = [la.row_from_assignment(a) for a in assigns]
    la.print_table(assign_rows)


def _normalize_course_stem(raw: str) -> str:
    """Return a readable, filesystem-friendly stem like 'linear_algebra'.

    Heuristics:
    - If there's an em dash '—', use the part after it.
    - Else if there are hyphens '-', use the part after the last hyphen if it contains letters.
    - Fallback to the whole string.
    - Lowercase, replace non-alphanumerics with spaces, collapse to underscores.
    """
    import re as _re
    s = raw or "course"
    if "—" in s:
        s = s.split("—", 1)[1].strip()
    elif "-" in s:
        tail = s.rsplit("-", 1)[-1].strip()
        if _re.search(r"[A-Za-z]", tail):
            s = tail
    # normalize
    s = _re.sub(r"[^A-Za-z0-9]+", " ", s)
    s = _re.sub(r"\s+", "_", s).strip("_").lower()
    return s or "course"


def cmd_fetch(argv: list[str]):
    cfg = load_config_from_cwd_or_die()
    token = cfg.get("token")
    cid = cfg.get("course_id")
    if not token or not cid:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    # Determine output directory (announce before any downloads/API fetches)
    outdir_arg: Optional[str] = None
    if argv:
        # Treat first positional as desired output directory
        outdir_arg = argv[0]
    if outdir_arg:
        outdir = Path(outdir_arg).expanduser().resolve()
    else:
        # Use course name based directory with _downloads suffix
        # We need course name; validate access to get it
        cname = assert_course_access(str(token), int(cid))
        stem = _normalize_course_stem(cname)
        outdir = Path.cwd() / f"{stem}_downloads"

    # Inform about output directory state and ensure it exists
    if outdir.exists():
        print(f"Output directory exists: {outdir}")
    else:
        print(f"Creating output directory: {outdir}")
        outdir.mkdir(parents=True, exist_ok=True)

    print(f"Saving to: {outdir}")

    # If we didn't already validate to derive the course name, validate now
    if outdir_arg:
        _ = assert_course_access(str(token), int(cid))

    print("Fetching PDFs...")
    import fetch_pdfs as fp
    pdf_count = fp.fetch_pdfs_to(sess, str(cid), outdir)
    print(f"PDFs downloaded: {pdf_count}")

    print("\nFetching assignment attachments and linked files...")
    import fetch_assignments as fa
    fa.fetch_assignment_files(sess, str(cid), outdir)
    print("\nDone.")


def main():
    if len(sys.argv) == 1:
        cmd_status()
        return

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd in ("pick",):
        cmd_pick(rest)
        return
    if cmd in ("ls", "list"):
        cmd_ls()
        return
    if cmd in ("fetch",):
        cmd_fetch(rest)
        return

    print(f"Unknown command: {cmd}")
    print("Usage: canvas.py [pick|ls|fetch [OUTDIR]]")
    sys.exit(2)


if __name__ == "__main__":
    main()
