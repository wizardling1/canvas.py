from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .client import CanvasClient


def list_calendar_events(
    client: CanvasClient,
    course_id: int,
    *,
    days_before: int = 30,
    days_after: int = 180,
) -> list[dict[str, Any]]:
    today = date.today()
    return list(
        client.iter_items(
            "/calendar_events",
            params={
                "per_page": 100,
                "context_codes[]": [f"course_{course_id}"],
                "start_date": (today - timedelta(days=days_before)).isoformat(),
                "end_date": (today + timedelta(days=days_after)).isoformat(),
            },
        )
    )

