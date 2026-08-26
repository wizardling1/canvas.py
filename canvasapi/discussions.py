from __future__ import annotations

from typing import Any

from .client import CanvasClient


def list_discussions(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    return list(
        client.iter_items(
            f"/courses/{course_id}/discussion_topics",
            params={"per_page": 100, "include[]": ["all_dates", "overrides"]},
        )
    )

