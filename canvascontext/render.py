from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class _MarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"p", "div", "section", "article"}:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("`")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "a":
            self.parts.append("[")
            self.links.append(attributes.get("href"))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("`")
        elif tag == "a":
            href = self.links.pop() if self.links else None
            self.parts.append(f"]({href})" if href else "]")
        elif tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_markdown(source: str | None) -> str:
    if not source:
        return ""
    parser = _MarkdownParser()
    parser.feed(source)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_slug(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:100] or fallback


def frontmatter(**values: Any) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is not None and value != "":
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def resource_path(
    directory: str, canvas_id: str | int, title: str, suffix: str = ".md"
) -> Path:
    return Path(directory) / f"{canvas_id}-{safe_slug(title)}{suffix}"


def render_course(course: dict[str, Any], synced_at: str) -> str:
    title = course.get("name") or course.get("course_code") or "Course"
    term = (course.get("term") or {}).get("name") or ""
    return (
        frontmatter(
            canvas_type="course",
            canvas_id=course.get("id"),
            source_url=course.get("html_url"),
            updated_at=course.get("updated_at"),
        )
        + f"# {title}\n\n"
        + (f"- Term: {term}\n" if term else "")
        + f"- Course code: {course.get('course_code') or ''}\n"
        + f"- Canvas ID: {course.get('id')}\n"
    )


def render_page(page: dict[str, Any], course_id: int, synced_at: str) -> str:
    title = page.get("title") or "Untitled page"
    return (
        frontmatter(
            canvas_type="page",
            canvas_id=page.get("page_id"),
            course_id=course_id,
            source_url=page.get("html_url"),
            updated_at=page.get("updated_at"),
        )
        + f"# {title}\n\n"
        + html_to_markdown(page.get("body"))
        + "\n"
    )


def render_assignment(
    assignment: dict[str, Any], course_id: int, synced_at: str
) -> str:
    title = assignment.get("name") or "Assignment"
    submission = assignment.get("submission") or {}
    details = [
        f"- Due: {assignment.get('due_at') or 'No due date'}",
        f"- Points: {assignment.get('points_possible')}",
        f"- Submission status: {submission.get('workflow_state') or 'unknown'}",
        f"- Missing: {bool(submission.get('missing'))}",
        f"- Late: {bool(submission.get('late'))}",
        f"- Score: {submission.get('score')}",
    ]
    return (
        frontmatter(
            canvas_type="assignment",
            canvas_id=assignment.get("id"),
            course_id=course_id,
            source_url=assignment.get("html_url"),
            updated_at=assignment.get("updated_at"),
        )
        + f"# {title}\n\n"
        + "\n".join(details)
        + "\n\n"
        + html_to_markdown(assignment.get("description"))
        + "\n"
    )


def render_module(
    module: dict[str, Any],
    course_id: int,
    synced_at: str,
    local_files: dict[int, Path],
) -> str:
    title = module.get("name") or "Module"
    lines = [
        frontmatter(
            canvas_type="module",
            canvas_id=module.get("id"),
            course_id=course_id,
            updated_at=module.get("updated_at"),
        ),
        f"# {title}",
        "",
        f"- State: {module.get('state') or 'unknown'}",
        f"- Unlocks: {module.get('unlock_at') or 'available'}",
        "",
        "## Items",
        "",
    ]
    for item in module.get("items") or []:
        item_title = item.get("title") or item.get("type") or "Item"
        target = item.get("html_url") or item.get("external_url") or ""
        if item.get("type") == "File" and item.get("content_id"):
            local = local_files.get(int(item["content_id"]))
            if local:
                target = f"../{local.as_posix()}"
        label = f"[{item_title}]({target})" if target else item_title
        detail = item.get("type") or "Unknown"
        completion = item.get("completion_requirement") or {}
        if completion:
            detail += f"; requirement={completion.get('type')}"
            if completion.get("completed") is not None:
                detail += f"; completed={completion.get('completed')}"
        lines.append(f"- {label} ({detail})")
    return "\n".join(lines).rstrip() + "\n"


def render_announcement(
    announcement: dict[str, Any], course_id: int, synced_at: str
) -> str:
    title = announcement.get("title") or "Announcement"
    return (
        frontmatter(
            canvas_type="announcement",
            canvas_id=announcement.get("id"),
            course_id=course_id,
            source_url=announcement.get("html_url"),
            updated_at=announcement.get("updated_at") or announcement.get("posted_at") or announcement.get("created_at"),
        )
        + f"# {title}\n\n"
        + html_to_markdown(announcement.get("message"))
        + "\n"
    )


def render_discussion(
    discussion: dict[str, Any], course_id: int, synced_at: str
) -> str:
    title = discussion.get("title") or "Discussion"
    return (
        frontmatter(
            canvas_type="discussion",
            canvas_id=discussion.get("id"),
            course_id=course_id,
            source_url=discussion.get("html_url"),
            updated_at=discussion.get("last_reply_at") or discussion.get("posted_at"),
        )
        + f"# {title}\n\n"
        + html_to_markdown(discussion.get("message"))
        + "\n"
    )
