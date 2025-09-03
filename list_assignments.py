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

import sys, json, re, argparse, time
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from tabulate import tabulate

API_BASE = "https://wsu.instructure.com/api/v1"

# Note: keep module import-safe; no config or session setup at import time.


def parse_links(h: str) -> Dict[str, str]:
    """Parse RFC 5988 Link header into dict(rel -> url)."""
    out: Dict[str, str] = {}
    if not h:
        return out
    for part in h.split(","):
        m = re.search(r'<([^>]+)>\s*;\s*rel="([^"]+)"', part.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def iso_to_local(iso: str | None) -> str:
    """Canvas returns ISO8601 UTC strings like '2025-09-21T23:59:00Z'.
    Return human-readable local time 'YYYY-MM-DD HH:MM (TZ)'."""
    if not iso:
        return ""
    s = iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()  # convert to local timezone
    return local.strftime("%Y-%m-%d %H:%M (%Z)")


def fetch_assignments(sess: requests.Session, course_id: str, include_unpublished: bool) -> List[Dict[str, Any]]:
    """Return list of assignments with submission included."""
    url = (f"{API_BASE}/courses/{course_id}/assignments"
           f"?per_page=100&include[]=submission&order_by=due_at")
    results: List[Dict[str, Any]] = []
    while url:
        r = sess.get(url, timeout=30)
        if r.status_code in (401, 403):
            sys.exit(f"Unauthorized ({r.status_code}). "
                     "Check token, course access, or that the course isn’t concluded.")
        if r.status_code == 404:
            sys.exit("Course not found (404). Check course_id.")
        r.raise_for_status()
        page = r.json()
        # Optionally filter to published/visible
        if include_unpublished:
            results.extend(page)
        else:
            for a in page:
                if not a.get("published", True):
                    continue
                if a.get("locked_for_user", False):
                    continue
                results.append(a)

        url = parse_links(r.headers.get("Link", "")).get("next")
        if url:
            time.sleep(0.1)
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


def main():
    ap = argparse.ArgumentParser(description="List Canvas assignments for a course.")
    ap.add_argument("--format", choices=["table", "csv", "json"], default="table",
                    help="Output format (default: table)")
    ap.add_argument("--all", action="store_true",
                    help="Include unpublished/locked assignments")
    args = ap.parse_args()

    cfg_path = Path(__file__).with_name("canvas_config.json")
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
