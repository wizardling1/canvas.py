from __future__ import annotations

from typing import Any

import requests

from .client import CanvasClient


def list_course_files(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    return list(
        client.iter_items(
            f"/courses/{course_id}/files", params={"per_page": 100}
        )
    )


def get_file(client: CanvasClient, file_id: int) -> dict[str, Any]:
    return client.get_json(f"/files/{file_id}")


def open_download(
    client: CanvasClient,
    metadata: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    url = metadata.get("download_url") or metadata.get("url")
    if not url:
        raise ValueError("Canvas file has no download URL")
    return client.request(
        "GET", url, headers=headers, stream=True, timeout=180
    )

