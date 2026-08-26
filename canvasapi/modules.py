from __future__ import annotations

from typing import Any

from .client import CanvasClient


def list_module_items(
    client: CanvasClient, course_id: int, module_id: int
) -> list[dict[str, Any]]:
    return list(
        client.iter_items(
            f"/courses/{course_id}/modules/{module_id}/items",
            params={"per_page": 100, "include[]": ["content_details"]},
        )
    )


def list_modules(
    client: CanvasClient,
    course_id: int,
    *,
    include_items: bool = True,
) -> list[dict[str, Any]]:
    include = ["items", "content_details"] if include_items else []
    modules = list(
        client.iter_items(
            f"/courses/{course_id}/modules",
            params={"per_page": 100, "include[]": include},
        )
    )
    if include_items:
        for module in modules:
            if module.get("items") is None:
                module["items"] = list_module_items(
                    client, course_id, int(module["id"])
                )
    return modules

