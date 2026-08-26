from __future__ import annotations

from typing import Any

from .client import CanvasClient


def list_announcements(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    return list(
        client.iter_items(
            "/announcements",
            params={"per_page": 100, "context_codes[]": [f"course_{course_id}"]},
        )
    )

