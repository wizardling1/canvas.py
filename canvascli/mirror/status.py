from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _assignment_line(assignment: dict[str, Any]) -> str:
    due = _datetime(assignment.get("due_at"))
    due_text = due.astimezone().strftime("%Y-%m-%d %H:%M %Z") if due else "no due date"
    return f"- {assignment.get('name') or 'Assignment'} — {due_text}"


def _course_grade(course: dict[str, Any]) -> str:
    for enrollment in course.get("enrollments") or []:
        for key in (
            "computed_current_score",
            "current_score",
            "computed_current_grade",
            "current_grade",
        ):
            value = enrollment.get(key)
            if value is not None:
                suffix = "%" if "score" in key else ""
                return f"{value}{suffix}"
    return "Not available"


def generate_status(
    *,
    course: dict[str, Any],
    assignments: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    synced_at: str,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    submission_map = {
        int(submission["assignment_id"]): submission
        for submission in submissions
        if submission.get("assignment_id") is not None
    }
    missing: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    awaiting_grade: list[dict[str, Any]] = []
    graded: list[dict[str, Any]] = []

    for assignment in assignments:
        if not assignment.get("published", True) or assignment.get("locked_for_user"):
            continue
        submission = assignment.get("submission") or submission_map.get(
            int(assignment["id"]), {}
        )
        if submission.get("excused"):
            continue
        state = submission.get("workflow_state") or "unsubmitted"
        due = _datetime(assignment.get("due_at"))
        if submission.get("missing"):
            missing.append(assignment)
        elif due and due < current and state == "unsubmitted":
            overdue.append(assignment)
        elif due and current <= due <= current + timedelta(days=14):
            upcoming.append(assignment)
        if state in {"submitted", "pending_review"}:
            awaiting_grade.append(assignment)
        elif state == "graded":
            graded.append(assignment)

    def section(title: str, items: list[dict[str, Any]]) -> list[str]:
        return [f"## {title}", ""] + (
            [_assignment_line(item) for item in sorted(items, key=lambda a: a.get("due_at") or "")]
            if items
            else ["None."]
        ) + [""]

    title = course.get("name") or course.get("course_code") or "Course"
    lines = [
        "---",
        'generated: true',
        f'synced_at: "{synced_at}"',
        "---",
        "",
        f"# {title} status",
        "",
        f"- Last Canvas sync: {synced_at}",
        f"- Current Canvas grade: {_course_grade(course)}",
        f"- Published assignments: {sum(bool(a.get('published', True)) for a in assignments)}",
        "",
    ]
    lines += section("Missing", missing)
    lines += section("Overdue", overdue)
    lines += section("Due within 14 days", upcoming)
    lines += section("Submitted, awaiting grade", awaiting_grade)
    lines += section("Graded", graded[-10:])

    lines.extend(["## Module progress", ""])
    if modules:
        for module in modules:
            lines.append(
                f"- {module.get('name') or 'Module'}: {module.get('state') or 'unknown'}"
            )
    else:
        lines.append("No visible modules.")

    materials: list[str] = []
    for module in modules:
        for item in module.get("items") or []:
            if item.get("type") in {"File", "Page", "ExternalUrl"}:
                materials.append(
                    f"- {item.get('title') or 'Untitled'} ({item.get('type')}; module: {module.get('name')})"
                )
    lines.extend(["", "## Module materials / candidate readings", ""])
    lines.extend(materials or ["No visible module reading materials."])

    lines.extend(["", "## Recent announcements", ""])
    recent = sorted(
        announcements,
        key=lambda item: item.get("posted_at") or item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )[:10]
    if recent:
        for announcement in recent:
            lines.append(
                f"- {announcement.get('title') or 'Announcement'} — "
                f"{announcement.get('posted_at') or announcement.get('updated_at') or announcement.get('created_at') or ''}"
            )
    else:
        lines.append("No visible announcements.")
    return "\n".join(lines).rstrip() + "\n"
