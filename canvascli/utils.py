from __future__ import annotations

import re
from pathlib import Path


def parse_links(header: str | None) -> dict[str, str]:
    links: dict[str, str] = {}
    if not header:
        return links
    for part in header.split(","):
        m = re.search(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if m:
            links[m.group(2)] = m.group(1)
    return links


def normalize_course_stem(raw: str) -> str:
    """Return a filesystem-friendly stem like 'linear_algebra'."""
    s = raw or "course"
    if "—" in s:
        s = s.split("—", 1)[1].strip()
    elif "-" in s:
        tail = s.rsplit("-", 1)[-1].strip()
        if re.search(r"[A-Za-z]", tail):
            s = tail
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    s = re.sub(r"\s+", "_", s).strip("_").lower()
    return s or "course"


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._\-()]", "_", name).strip()

