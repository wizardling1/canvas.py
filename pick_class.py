#!/usr/bin/env python3
"""
pick_class.py — list your Canvas courses and store a chosen course_id in canvas_config.json

Requirements: requests
Config file (current working directory): {"token": "...", "course_id": "...optional..."}

Usage:
  python3 pick_class.py               # list ACTIVE enrollments (current/ongoing)
  python3 pick_class.py --all         # include past/future/ended courses
  python3 pick_class.py --search math # filter by name/code
  python3 pick_class.py --set-id 123  # non-interactive, set course_id directly
"""
import sys, json
from pathlib import Path
import requests
from canvascli.api import iter_paginated
from typing import Dict, List, Optional
from tabulate import tabulate

API_BASE = "https://wsu.instructure.com/api/v1"
from canvascli import utils as _cu

# ---------- helpers ----------
def load_config(cfg_path: Path) -> Dict:
    if not cfg_path.exists():
        sys.exit(f"Missing canvas_config.json at {cfg_path}")
    try:
        return json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid canvas_config.json: {e}")

def save_config(cfg_path: Path, cfg: Dict):
    tmp = cfg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    tmp.replace(cfg_path)

def get_courses(sess: requests.Session, include_all: bool, search: str | None) -> List[Dict]:
    params = {
        "per_page": 100,
        "include[]": ["term"],  # include term info
        "enrollment_state": "active" if not include_all else None,
    }
    url = f"{API_BASE}/courses"
    out: List[Dict] = []
    try:
        for page in iter_paginated(sess, url, params={k: v for k, v in params.items() if v is not None}):
            out.extend(page)
    except requests.HTTPError as e:
        if getattr(e, "response", None) is not None and e.response.status_code in (401, 403):
            sys.exit(f"Courses API unauthorized ({e.response.status_code}). Check your token.")
        raise
    # optional search filter (case-insensitive on name/code)
    if search:
        s = search.lower()
        out = [c for c in out if s in (c.get("name","").lower()) or s in (c.get("course_code","").lower())]
    # stable-ish sort: by term start_at (if present) then name
    def term_key(c):
        t = (c.get("term") or {})
        return (t.get("start_at") or "9999"), c.get("name") or ""
    out.sort(key=term_key)
    return out

def fmt_term(c: Dict) -> str:
    t = c.get("term") or {}
    name = t.get("name") or ""
    return name

def print_courses(courses: List[Dict]):
    if not courses:
        print("No courses found for the chosen filters.")
        return
    rows = []
    for i, c in enumerate(courses, 1):
        rows.append([
            i,
            c.get("id"),
            fmt_term(c),
            c.get("course_code") or "",
            c.get("name") or "",
        ])
    print(tabulate(rows, headers=["#", "id", "term", "code", "name"], tablefmt="github", disable_numparse=True))

# ---------- main ----------
def run(include_all: bool = False, search: Optional[str] = None, set_id: Optional[int] = None) -> None:
    """Programmatic entry point to pick or set the active Canvas course.

    - If set_id is provided, update canvas_config.json non-interactively and return.
    - Otherwise, list courses and prompt the user to pick one.
    """
    # Always use the caller's current working directory for canvas_config.json
    cfg_path = Path.cwd() / "canvas_config.json"
    cfg = load_config(cfg_path)
    token = cfg.get("token")
    if not token:
        sys.exit("canvas_config.json must include a 'token' field.")

    if set_id:
        cfg["course_id"] = int(set_id)
        save_config(cfg_path, cfg)
        print(f"Saved course_id={set_id} to {cfg_path}")
        return

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    courses = get_courses(sess, include_all=include_all, search=search)
    if not courses:
        print("No courses to display. Try --all or adjust --search.")
        sys.exit(0)

    print("\nYour Canvas courses:\n")
    print_courses(courses)
    print()

    # Prompt until valid selection
    while True:
        choice = input(f"Enter a number 1-{len(courses)} (or 'q' to quit): ").strip().lower()
        if choice in ("q", "quit", "exit"):
            print("Aborted. Nothing saved.")
            return
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(courses):
                picked = courses[idx-1]
                cid = picked.get("id")
                name = picked.get("name") or picked.get("course_code") or str(cid)
                cfg["course_id"] = int(cid)
                save_config(cfg_path, cfg)
                print(f"\nSaved course_id={cid} ({name}) to {cfg_path}")
                return
        print("Invalid selection. Please try again.")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Pick a Canvas course and store course_id in canvas_config.json (in current directory)")
    ap.add_argument("--all", action="store_true", help="Include past/future/ended enrollments (not just active)")
    ap.add_argument("--search", help="Filter course list by name or code (case-insensitive)")
    ap.add_argument("--set-id", type=int, help="Non-interactive: set course_id directly and exit")
    args = ap.parse_args()

    run(include_all=args.all, search=args.search, set_id=args.set_id)

if __name__ == "__main__":
    main()
