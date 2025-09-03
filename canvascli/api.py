from __future__ import annotations

from typing import Optional, Iterable, Any
import requests
import time
from .utils import parse_links

API_BASE = "https://wsu.instructure.com/api/v1"


def create_session(token: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {token}"})
    return sess


def get_course_name(sess: requests.Session, course_id: int) -> str:
    r = sess.get(f"{API_BASE}/courses/{course_id}", timeout=20)
    if r.status_code in (401, 403):
        raise SystemExit(
            f"Unauthorized to access course {course_id}. Check your Canvas token and permissions."
        )
    if r.status_code == 404:
        raise SystemExit(f"Course {course_id} not found. Check course_id in canvas_config.json.")
    r.raise_for_status()
    data = r.json()
    return data.get("name") or data.get("course_code") or "(unnamed course)"


def iter_paginated(
    sess: requests.Session,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    stop_status: tuple[int, ...] = (),
    max_pages: int = 200,
    sleep_seconds: float = 0.1,
) -> Iterable[Any]:
    """
    Iterate through Canvas-style paginated endpoints.

    - Yields each page's JSON payload (usually a list).
    - Follows RFC5988 Link headers.
    - If response status is in stop_status, stops quietly without raising.
    - Guards against infinite loops and too many pages.
    """
    seen: set[str] = set()
    pages = 0
    first_params = params
    while url:
        if url in seen:
            break
        seen.add(url)
        pages += 1
        if pages > max_pages:
            break

        r = sess.get(url, params=first_params, timeout=30)
        first_params = None  # only apply params on the first request
        if r.status_code in stop_status:
            return
        r.raise_for_status()
        yield r.json()
        url = parse_links(r.headers.get("Link", "")).get("next")
        if url:
            time.sleep(sleep_seconds)
