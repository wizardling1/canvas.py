from __future__ import annotations

from pathlib import Path

from .render import frontmatter
from .storage import write_text_if_changed


def add_transcript(
    *,
    course_root: Path,
    course_id: int,
    date: str,
    text: str,
) -> Path:
    directory = course_root / "transcripts"
    destination = directory / f"{date}.md"
    document = (
        frontmatter(
            canvas_type="lecture_transcript",
            course_id=course_id,
            lecture_date=date,
            title=date,
            source="user-provided",
        )
        + f"# {date}\n\n"
        + text.strip()
        + "\n"
    )
    write_text_if_changed(destination, document)
    return destination
