#!/usr/bin/env python3
"""
List Canvas assignments for a course with useful details.

Reads canvas_config.json next to this script with:
{
  "token": "<YOUR_CANVAS_TOKEN>",
  "course_id": 123456,
  "outdir": "./downloads"   # optional, ignored here
}

Usage:
  python list_assignments.py               # pretty table
  python list_assignments.py --format csv  # CSV
  python list_assignments.py --format json # JSON
  python list_assignments.py --all         # include unpublished/locked
"""

import sys, json, argparse
from pathlib import Path
from typing import List, Dict, Any

import requests
from tabulate import tabulate

API_BASE = "https://wsu.instructure.com/api/v1"

# Note: keep module import-safe; no config or session setup at import time.

from canvascli.formatting import iso_to_local
from canvascli.parsing import HelpfulArgumentParser
from canvasapi import iter_paginated


def fetch_assignments(sess: requests.Session, course_id: str, include_unpublished: bool) -> List[Dict[str, Any]]:
    """Return list of assignments with submission included."""
    url = (f"{API_BASE}/courses/{course_id}/assignments"
           f"?per_page=100&include[]=submission&order_by=due_at")
    results: List[Dict[str, Any]] = []
    try:
        for page in iter_paginated(sess, url):
            if include_unpublished:
                results.extend(page)
            else:
                for a in page:
                    if not a.get("published", True):
                        continue
                    if a.get("locked_for_user", False):
                        continue
                    results.append(a)
    except requests.HTTPError as e:
        if getattr(e, "response", None) is not None and e.response.status_code in (401, 403):
            sys.exit(
                f"Unauthorized ({e.response.status_code}). Check token, course access, or that the course isn’t concluded."
            )
        if getattr(e, "response", None) is not None and e.response.status_code == 404:
            sys.exit("Course not found (404). Check course_id.")
        raise
    return results


def row_from_assignment(a: dict) -> dict:
    sub = a.get("submission") or {}
    return {
        "name": a.get("name", ""),
        "due": iso_to_local(a.get("due_at")),
        "points": a.get("points_possible"),
        "score": sub.get("score"),
        "workflow_state": sub.get("workflow_state", ""),  # 'submitted', 'graded', 'unsubmitted', etc.
        "late": sub.get("late", False),
        "missing": sub.get("missing", False),
        "published": a.get("published", False),
        # extra fields (not shown in table by default)
        "grading_type": a.get("grading_type", ""),
        "locked_for_user": a.get("locked_for_user", False),
        "html_url": a.get("html_url", ""),
        "submitted_at": iso_to_local(sub.get("submitted_at")),
        "graded_at": iso_to_local(sub.get("graded_at")),
        "excused": sub.get("excused", False),
    }


# ---------- pretty printers ----------
def print_table(rows: List[Dict[str, Any]]):
    """Pretty table with tabulate only."""
    if not rows:
        print("No assignments found.")
        return

    columns = [
        ("name", "name"),
        ("due", "due"),
        ("points", "points"),
        ("score", "score"),
        ("workflow_state", "workflow_state"),
        ("late", "late"),
        ("missing", "missing"),
        ("published", "published"),
    ]

    headers = [title for _, title in columns]
    data = []
    for r in rows:
        row = []
        for key, _ in columns:
            v = r.get(key, "")
            if v is None:
                v = ""
            if isinstance(v, bool):
                v = "True" if v else "False"
            if key in ("points", "score") and isinstance(v, (int, float)):
                v = f"{v:.1f}"
            row.append(v)
        data.append(row)

    print(tabulate(data, headers=headers, tablefmt="github", disable_numparse=True))


def print_csv(rows: List[Dict[str, Any]]):
    import csv, sys as _sys
    # ensure stable field order
    fieldnames = [
        "name","due","points","score","workflow_state","late","missing","published",
        "grading_type","locked_for_user","html_url","submitted_at","graded_at","excused"
    ]
    writer = csv.DictWriter(_sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)


def _parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        description=(
            "List assignments and your submission state for the configured "
            "Canvas course."
        ),
        epilog=(
            "examples:\n"
            "  python3 list_assignments.py\n"
            "  python3 list_assignments.py --format json\n"
            "  python3 list_assignments.py --all --format csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("canvas_config.json"),
        metavar="PATH",
        help="Canvas JSON config containing token and course_id (default: next to this script)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include unpublished and user-locked assignments",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    cfg_path = args.config.expanduser().resolve()
    if not cfg_path.exists():
        sys.exit(f"Missing canvas_config.json. Please create {cfg_path}")

    try:
        config = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid canvas_config.json: {e}")

    token = config.get("token")
    course_id = str(config.get("course_id") or "")
    if not token or not course_id:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    assignments = fetch_assignments(sess, course_id, include_unpublished=args.all)
    rows = [row_from_assignment(a) for a in assignments]

    if args.format == "json":
        print(json.dumps(rows, indent=2))
    elif args.format == "csv":
        print_csv(rows)
    else:
        print_table(rows)
        if len(rows) <= 5:
            for r in rows:
                if r.get("html_url"):
                    print(f"  - {r['name']}: {r['html_url']}")


if __name__ == "__main__":
    main()
