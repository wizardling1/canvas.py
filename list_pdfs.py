#!/usr/bin/env python3
import argparse
import sys, re, json
from pathlib import Path
import requests
from tabulate import tabulate

API_BASE = "https://wsu.instructure.com/api/v1"

# No config/session side effects at import time; standalone config handled in main().

from canvasapi import iter_paginated
from canvascli.formatting import human_size, iso_to_local
from canvascli.parsing import HelpfulArgumentParser

# ---------- helpers ----------
def iso_dt(s):
    return iso_to_local(s)

def is_pdf_name(name: str):
    return bool(re.search(r"\.pdf$", name or "", re.IGNORECASE))

# ---------- collectors ----------
def iter_module_file_ids(sess: requests.Session, course_id: str):
    """Yield file IDs (and titles) referenced from modules (visible to students)."""
    url = f"{API_BASE}/courses/{course_id}/modules?include[]=items&per_page=100"
    for page in iter_paginated(sess, url, stop_status=(401, 403)):
        for mod in page:
            for it in (mod.get("items") or []):
                if it.get("type") == "File":
                    fid = it.get("content_id")
                    title = it.get("title") or ""
                    if fid:
                        yield int(fid), title

def get_file_meta(sess: requests.Session, file_id: int):
    r = sess.get(f"{API_BASE}/files/{file_id}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def iter_course_pdfs_files_api(sess: requests.Session, course_id: str):
    """
    Query the course Files endpoint for PDFs directly.
    This may 403 for students depending on course settings.
    """
    url = (f"{API_BASE}/courses/{course_id}/files"
           f"?content_types[]=application/pdf&per_page=100&sort=updated_at&order=desc")
    for page in iter_paginated(sess, url, stop_status=(401, 403)):
        for f in page:
            yield f

# ---------- main ----------
def collect_pdfs(sess: requests.Session, course_id: str):
    """Return a list of PDF rows for the given course_id using the provided session."""
    seen = {}
    rows = []

    # 1) Files referenced from Modules (often student-visible)
    for fid, title in iter_module_file_ids(sess, course_id):
        meta = get_file_meta(sess, fid)
        if not meta:
            continue
        name = meta.get("display_name") or meta.get("filename") or title
        if not (is_pdf_name(name) or (meta.get("mime_class") == "pdf")):
            continue
        dl = meta.get("download_url") or meta.get("url")  # both are signed
        rows.append({
            "id": meta.get("id"),
            "name": name,
            "size": meta.get("size") or 0,
            "updated_at": meta.get("updated_at") or meta.get("modified_at"),
            "download_url": dl,
            "source": "modules"
        })
        seen[meta["id"]] = True

    # 2) (Best effort) Files area (may be forbidden to students)
    for f in iter_course_pdfs_files_api(sess, course_id):
        fid = f.get("id")
        if not fid or fid in seen:
            continue
        name = f.get("display_name") or f.get("filename") or ""
        if not (is_pdf_name(name) or f.get("mime_class") == "pdf"):
            continue
        dl = f.get("download_url") or f.get("url")
        rows.append({
            "id": fid,
            "name": name,
            "size": f.get("size") or 0,
            "updated_at": f.get("updated_at") or f.get("modified_at"),
            "download_url": dl,
            "source": "files"
        })
        seen[fid] = True

    # Sort newest first
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return rows


def print_pdfs_table(rows):
    if not rows:
        print("No PDFs found (or not visible with your account/permissions).")
        print("Tip: instructors often publish PDFs through Modules; if Modules are unpublished,")
        print("they won’t show up. You can still download by known file_id via /api/v1/files/{id}.")
        return

    print(f"Found {len(rows)} PDF(s):\n")
    headers = ["ID", "Name", "Size", "Updated", "Src"]
    data = []
    for r in rows:
        data.append([
            r.get("id"),
            r.get("name"),
            human_size(int(r.get("size") or 0)),
            iso_dt(r.get("updated_at")),
            r.get("source"),
        ])
    print(tabulate(data, headers=headers, tablefmt="github", disable_numparse=True))
    print()
    for r in rows:
        if r.get("download_url"):
            print(f"- {r['name']}: {r['download_url']}")
    print()


def _parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        description=(
            "List PDF files visible through Canvas modules and the course Files "
            "API, including their download URLs."
        ),
        epilog=(
            "examples:\n"
            "  python3 list_pdfs.py\n"
            "  python3 list_pdfs.py --config ./canvas_config.json"
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    cfg_path = args.config.expanduser().resolve()
    if not cfg_path.exists():
        sys.exit(f"Missing {cfg_path}. Example:\n{{\"token\":\"...\",\"course_id\":1864601}}")

    try:
        config = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in {cfg_path}: {e}")

    token = config.get("token")
    course_id = config.get("course_id")
    if not token or not course_id:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    course_id = str(course_id)

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    rows = collect_pdfs(sess, course_id)
    print_pdfs_table(rows)

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        detail = f" ({resp.status_code})" if resp is not None else ""
        print(f"HTTP error{detail}: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
