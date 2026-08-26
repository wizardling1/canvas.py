from __future__ import annotations

from typing import Any

from .client import CanvasClient


def list_assignments(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    return list(
        client.iter_items(
            f"/courses/{course_id}/assignments",
            params={
                "per_page": 100,
                "include[]": ["description", "submission", "overrides"],
                "order_by": "due_at",
            },
        )
    )

