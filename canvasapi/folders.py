from __future__ import annotations

from typing import Any

from .client import CanvasClient


def list_course_folders(
    client: CanvasClient, course_id: int
) -> list[dict[str, Any]]:
    """Return every folder in a course as provided by Canvas."""
    return list(
        client.iter_items(
            f"/courses/{course_id}/folders", params={"per_page": 100}
        )
    )


def get_folder(client: CanvasClient, folder_id: int) -> dict[str, Any]:
    """Return one folder by its Canvas ID."""
    return client.get_json(f"/folders/{folder_id}")
