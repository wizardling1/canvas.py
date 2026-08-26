from __future__ import annotations

from typing import Any

from .client import CanvasClient


def list_my_submissions(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    return list(
        client.iter_items(
            f"/courses/{course_id}/students/submissions",
            params={
                "per_page": 100,
                "include[]": [
                    "submission_comments",
                    "rubric_assessment",
                    "assignment",
                ],
            },
        )
    )

