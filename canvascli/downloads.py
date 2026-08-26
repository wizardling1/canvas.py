from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from canvasapi import CanvasClient
from canvasapi.files import open_download

from .utils import safe_filename

FILE_LINK_RE = re.compile(
    r"(?:/api/v1)?/files/(\d+)(?:/download|/preview|\b)", re.IGNORECASE
)


def assignment_file_ids(assignment: dict[str, Any]) -> set[int]:
    file_ids: set[int] = set()
    for attachment in assignment.get("attachments") or []:
        if attachment.get("id") is not None:
            file_ids.add(int(attachment["id"]))
    description = assignment.get("description") or ""
    file_ids.update(int(match.group(1)) for match in FILE_LINK_RE.finditer(description))
    return file_ids


def conditional_download(
    client: CanvasClient,
    metadata: dict[str, Any],
    destination: Path,
) -> bool:
    """Download a Canvas file atomically. Return True when bytes changed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    etag_path = destination.with_suffix(destination.suffix + ".etag")
    headers: dict[str, str] = {}
    if etag_path.exists():
        etag = etag_path.read_text().strip()
        if etag:
            headers["If-None-Match"] = etag

    response = open_download(client, metadata, headers=headers)
    with response:
        if response.status_code == 304:
            print(f"Not modified: {destination}")
            return False
        temporary = destination.with_suffix(destination.suffix + ".part")
        with temporary.open("wb") as output:
            for chunk in response.iter_content(8192):
                if chunk:
                    output.write(chunk)
        temporary.replace(destination)
        etag = response.headers.get("ETag")
        if etag:
            etag_path.write_text(etag)
    print(f"Saved: {destination}")
    return True


def file_destination(
    directory: Path,
    metadata: dict[str, Any],
    used_names: dict[str, int] | None = None,
) -> Path:
    file_id = int(metadata["id"])
    raw_name = (
        metadata.get("display_name")
        or metadata.get("filename")
        or f"file_{file_id}"
    )
    name = safe_filename(str(raw_name)) or f"file_{file_id}"
    if used_names is not None:
        if name in used_names and used_names[name] != file_id:
            path = Path(name)
            name = f"{path.stem}__{file_id}{path.suffix}"
        used_names[name] = file_id
    return directory / name

