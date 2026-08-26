#!/usr/bin/env python3
"""
Download all available PDFs from a Canvas course (via Modules) into OUTDIR.

Uses ETag to avoid re-downloading unchanged files.
Note: This module is import-safe (no config reads at import time).
"""

import sys, json, time, re
from pathlib import Path
from typing import Optional, Dict, Iterable
import requests
from canvasapi import iter_paginated

API_BASE = "https://wsu.instructure.com/api/v1"

# ---- defaults (overridden in main() or fetch_pdfs_to) ----
OUTDIR = Path("./downloads").resolve()

from canvascli import utils as _cu


def iter_module_file_ids(sess: requests.Session, course_id: str) -> Iterable[int]:
    """Yield file IDs from all course modules."""
    url = f"{API_BASE}/courses/{course_id}/modules?include[]=items&per_page=100"
    for page in iter_paginated(sess, url):
        for mod in page:
            for item in (mod.get("items") or []):
                if item.get("type") == "File":
                    fid = item.get("content_id")
                    if isinstance(fid, int):
                        yield fid


def fetch_file_meta(sess: requests.Session, file_id: int) -> Optional[dict]:
    r = sess.get(f"{API_BASE}/files/{file_id}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def is_pdf(meta: dict) -> bool:
    ct = (meta.get("content-type") or meta.get("content_type") or "").lower()
    fname = (meta.get("filename") or meta.get("display_name") or "").lower()
    mime_class = (meta.get("mime_class") or "").lower()
    return "pdf" in mime_class or ct == "application/pdf" or fname.endswith(".pdf")


def pick_output_path(display_name: str, file_id: int, used_names: Dict[str, int]) -> Path:
    """Ensure a stable filename; only add __id if duplicate names collide."""
    base = _cu.safe_filename(display_name)
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    if base in used_names and used_names[base] != file_id:
        # Two different files with same name -> disambiguate
        base = f"{Path(base).stem}__{file_id}.pdf"
    used_names[base] = file_id
    return OUTDIR / base


def download_pdf(sess: requests.Session, meta: dict, used_names: Dict[str, int]) -> None:
    dl = meta.get("download_url") or meta.get("url")
    if not dl:
        print(f"Locked or no download_url for {meta.get('display_name')}")
        return

    out = pick_output_path(meta.get("display_name") or meta.get("filename") or str(meta["id"]),
                           meta["id"], used_names)
    etag_file = out.with_suffix(out.suffix + ".etag")

    headers = {}
    if etag_file.exists():
        et = etag_file.read_text().strip()
        if et:
            headers["If-None-Match"] = et

    with sess.get(dl, headers=headers, stream=True, timeout=180, allow_redirects=True) as resp:
        if resp.status_code == 304:
            print(f"Not modified: {out.name}")
            return
        if resp.status_code != 200:
            print(f"Failed {out.name}: HTTP {resp.status_code}")
            return

        it = resp.iter_content(chunk_size=8192)
        first = next(it, b"")
        if not first.startswith(b"%PDF-"):
            print(f"Non-PDF content for {out.name} — skipping.")
            return

        tmp = out.with_suffix(out.suffix + ".part")
        with open(tmp, "wb") as f:
            f.write(first)
            for chunk in it:
                if chunk:
                    f.write(chunk)
        tmp.replace(out)

        et = resp.headers.get("ETag")
        if et:
            etag_file.write_text(et)

        print(f"Saved: {out.name}")


def fetch_pdfs_to(sess: requests.Session, course_id: str, outdir: Path) -> int:
    global OUTDIR
    old_outdir = OUTDIR
    OUTDIR = outdir
    try:
        seen, used_names = set(), {}
        file_ids = [fid for fid in iter_module_file_ids(sess, course_id) if fid not in seen and not seen.add(fid)]

        if not file_ids:
            print("No module-linked files found (or modules hidden).")
            return 0

        metas = [m for fid in file_ids if (m := fetch_file_meta(sess, fid)) and is_pdf(m)]
        if not metas:
            print("No PDFs found.")
            return 0

        count = 0
        for m in metas:
            download_pdf(sess, m, used_names)
            time.sleep(0.1)
            count += 1
        return count
    finally:
        OUTDIR = old_outdir


def main():
    # Standalone execution: read config next to this script
    cfg_path = Path(__file__).with_name("canvas_config.json")
    if not cfg_path.exists():
        sys.exit(f"Missing canvas_config.json. Please create {cfg_path}")

    try:
        config = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid canvas_config.json: {e}")

    token = config.get("token")
    course_id = str(config.get("course_id") or "")
    outdir = Path(config.get("outdir", "./downloads")).resolve()

    if not token or not course_id:
        sys.exit("canvas_config.json must include 'token' and 'course_id'")

    outdir.mkdir(parents=True, exist_ok=True)

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})

    n = fetch_pdfs_to(sess, course_id, outdir)
    if n:
        print(f"\nDone. PDFs found: {n}. Saved to {outdir}")


if __name__ == "__main__":
    main()
