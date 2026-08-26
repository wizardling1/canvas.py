from __future__ import annotations

from typing import Any

from .client import CanvasClient


def get_current_user(client: CanvasClient) -> dict[str, Any]:
    return client.get_json("/users/self/profile")


def list_courses(
    client: CanvasClient,
    *,
    enrollment_state: str | None = "active",
    include: tuple[str, ...] = ("term",),
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"per_page": 100, "include[]": list(include)}
    if enrollment_state:
        params["enrollment_state"] = enrollment_state
    return list(client.iter_items("/courses", params=params))


def get_course(
    client: CanvasClient,
    course_id: int,
    *,
    include: tuple[str, ...] = (
        "term",
        "syllabus_body",
        "total_scores",
        "course_progress",
    ),
) -> dict[str, Any]:
    return client.get_json(
        f"/courses/{course_id}", params={"include[]": list(include)}
    )

