#!/usr/bin/env python3
"""
Download all homework-related attachments/linked files from a Canvas course.

- Creates downloads/<Assignment Name>/ and saves each file there
- Uses per-file ETag sidecars (*.etag) to avoid re-downloading
- Collects files from:
    1) assignment["attachments"] (when present)
    2) any <a href=".../files/<id>/..."> links in assignment["description"]

Note: Import-safe. No config reads at import time.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests
from canvasapi import iter_paginated

API_BASE = "https://wsu.instructure.com/api/v1"
OUTROOT = Path("./downloads")
from canvascli import utils as _cu



def slugify(name: str) -> str:
    name = (name or "").strip().replace("/", "-")
    name = re.sub(r"[^\w\-. ()\[\]]+", "_", name)
    return name[:120] or "assignment"


def iter_assignments(sess: requests.Session, course_id: str) -> Iterable[dict]:
    """Yield all assignments (with description)."""
    url = f"{API_BASE}/courses/{course_id}/assignments?per_page=100&include[]=description"
    try:
        for page in iter_paginated(sess, url):
            if not isinstance(page, list):
                sys.exit(f"Unexpected response from assignments API: {str(page)[:200]}")
            for a in page:
                yield a
    except requests.HTTPError as e:
        if getattr(e, "response", None) is not None and e.response.status_code in (401, 403):
            sys.exit(f"Assignments API unauthorized ({e.response.status_code}). Check token and course access.")
        raise


FILE_LINK_RE = re.compile(
    r"""(?:/api/v1)?/files/(\d+)(?:/download|/preview|\b)""",
    re.IGNORECASE,
)


def extract_file_ids_from_description(html: Optional[str]) -> List[int]:
    if not html:
        return []
    ids = set(int(m.group(1)) for m in FILE_LINK_RE.finditer(html))
    return sorted(ids)


def get_file_meta(sess: requests.Session, file_id: int) -> dict:
    r = sess.get(f"{API_BASE}/files/{file_id}", timeout=30)
    r.raise_for_status()
    return r.json()


def conditional_download(sess: requests.Session, url: str, out_path: Path) -> None:
    """Download to out_path if ETag changed."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    etag_path = out_path.with_suffix(out_path.suffix + ".etag")
    headers = {}
    if etag_path.exists():
        et = etag_path.read_text().strip()
        if et:
            headers["If-None-Match"] = et

    with sess.get(url, headers=headers, stream=True, timeout=120, allow_redirects=True) as resp:
        if resp.status_code == 304:
            print(f"Not modified: {out_path}")
            return
        if resp.status_code != 200:
            raise RuntimeError(f"Download failed ({resp.status_code}) for {out_path.name}: {resp.text[:200]}")

        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)
        tmp.replace(out_path)

        et = resp.headers.get("ETag")
        if et:
            etag_path.write_text(et)

    print(f"Saved: {out_path}")


def main():
    # Standalone execution: read config next to this script
    cfg_path = Path(__file__).with_name("canvas_config.json")
    if not cfg_path.exists():
        sys.exit(f"Missing config.json. Create {cfg_path} with {{\"token\":\"...\",\"course_id\":123}}")
    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in canvas_config.json: {e}")

    token = cfg.get("token")
    course_id = str(cfg.get("course_id") or "")
    if not token or not course_id:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    fetch_assignment_files(sess, course_id, OUTROOT)


def fetch_assignment_files(sess: requests.Session, course_id: str, outroot: Path) -> None:
    outroot.mkdir(parents=True, exist_ok=True)
    total_files = 0
    assignments_seen = 0

    for a in iter_assignments(sess, course_id):
        assignments_seen += 1
        name = a.get("name") or f"assignment_{a.get('id')}"
        assign_dir = outroot / slugify(name)

        # Collect file IDs from both attachments and description links
        file_ids: Set[int] = set()

        # 1) attachments (if Canvas uses them)
        for att in (a.get("attachments") or []):
            if "id" in att:
                try:
                    file_ids.add(int(att["id"]))
                except Exception:
                    pass

        # 2) links inside description HTML
        for fid in extract_file_ids_from_description(a.get("description")):
            file_ids.add(fid)

        if not file_ids:
            # nothing to fetch for this assignment
            continue

        print(f"\n== {name} ==")
        for fid in sorted(file_ids):
            try:
                meta = get_file_meta(sess, fid)
            except Exception as e:
                print(f"  Skipping file {fid}: cannot read metadata ({e})")
                continue

            disp = meta.get("display_name") or meta.get("filename") or f"file_{fid}"
            dl = meta.get("download_url") or meta.get("url")
            if not dl:
                print(f"  No download_url for {disp} (maybe locked).")
                continue

            target = assign_dir / disp
            try:
                conditional_download(sess, dl, target)
                total_files += 1
            except Exception as e:
                print(f"  Failed: {disp}: {e}")

    if assignments_seen == 0:
        print("No assignments visible to this token/course.")
    elif total_files == 0:
        print("No downloadable files found in any assignment (attachments or description links).")


if __name__ == "__main__":
    main()
