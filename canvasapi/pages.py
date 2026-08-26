from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .client import CanvasClient


def get_page(client: CanvasClient, path_or_url: str) -> dict[str, Any]:
    """Retrieve a page from either its API URL or a Canvas API path."""
    return client.get_json(path_or_url)


def list_pages(client: CanvasClient, course_id: int) -> list[dict[str, Any]]:
    summaries = list(
        client.iter_items(
            f"/courses/{course_id}/pages", params={"per_page": 100}
        )
    )
    pages: list[dict[str, Any]] = []
    for summary in summaries:
        identifier = summary.get("url") or f"page_id:{summary['page_id']}"
        pages.append(get_page(
            client, f"/courses/{course_id}/pages/{quote(str(identifier), safe=':')}"
        ))
    return pages
