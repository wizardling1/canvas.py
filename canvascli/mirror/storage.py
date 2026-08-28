from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

EPHEMERAL_CANVAS_FIELDS = {"seconds_late", "canvadoc_session_url"}


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes_if_changed(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return False
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return True


def write_text_if_changed(path: Path, text: str) -> bool:
    return write_bytes_if_changed(path, text.encode("utf-8"))


def write_json_if_changed(path: Path, value: Any) -> bool:
    return write_text_if_changed(
        path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def stable_canvas_data(value: Any) -> Any:
    """Remove response fields whose values change without source-content changes."""
    if isinstance(value, dict):
        return {
            key: stable_canvas_data(item)
            for key, item in value.items()
            if key not in EPHEMERAL_CANVAS_FIELDS
        }
    if isinstance(value, list):
        return [stable_canvas_data(item) for item in value]
    return value
