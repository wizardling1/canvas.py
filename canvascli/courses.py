from __future__ import annotations

from typing import Any, Iterable

from canvasapi import (
    CanvasAuthenticationError,
    CanvasAuthorizationError,
    CanvasClient,
    CanvasNotFoundError,
)
from canvasapi.courses import get_course, list_courses

from .diagnostics import CLIError
from .repository import CanvasRepository, CourseRecord, course_slug


def course_sort_key(course: dict[str, Any]) -> tuple[str, str, int]:
    term = course.get("term") or {}
    return (
        str(term.get("start_at") or "9999"),
        str(course.get("name") or course.get("course_code") or ""),
        int(course.get("id") or 0),
    )


def _validate_canvas_course(course: dict[str, Any], label: str) -> None:
    try:
        course_id = int(course.get("id"))
    except (TypeError, ValueError) as exc:
        raise CLIError(f"Canvas returned invalid metadata for {label}: missing course ID", exit_code=4) from exc
    if course_id <= 0:
        raise CLIError(f"Canvas returned invalid metadata for {label}: invalid course ID", exit_code=4)
    term = course.get("term")
    if term is not None and not isinstance(term, dict):
        raise CLIError(f"Canvas returned invalid metadata for course {course_id}: invalid term", exit_code=4)


def available_courses(client: CanvasClient, *, include_all: bool) -> list[dict[str, Any]]:
    courses = list_courses(
        client, enrollment_state=None if include_all else "active"
    )
    for index, course in enumerate(courses):
        _validate_canvas_course(course, f"courses[{index}]")
    courses.sort(key=course_sort_key)
    return courses


def validate_course_ids(
    client: CanvasClient, course_ids: Iterable[int]
) -> list[dict[str, Any]]:
    courses: list[dict[str, Any]] = []
    for course_id in course_ids:
        try:
            course = get_course(client, course_id)
        except CanvasAuthenticationError as exc:
            raise CLIError(
                f"cannot validate course {course_id}: Canvas rejected CANVAS_TOKEN",
                "the token may be invalid, expired, or issued by another Canvas installation",
                exit_code=3,
            ) from exc
        except CanvasAuthorizationError as exc:
            raise CLIError(
                f"cannot add course {course_id}: Canvas denied access",
                "check the account's enrollment and course permissions",
                exit_code=4,
            ) from exc
        except CanvasNotFoundError as exc:
            raise CLIError(
                f"cannot add course {course_id}: it does not exist or is not visible to this account",
                exit_code=4,
            ) from exc
        if not isinstance(course, dict) or course.get("id") is None:
            raise CLIError(f"Canvas returned invalid metadata for course {course_id}", exit_code=4)
        _validate_canvas_course(course, f"course {course_id}")
        if int(course["id"]) != course_id:
            raise CLIError(
                f"Canvas returned course {course['id']} while validating course {course_id}",
                exit_code=4,
            )
        courses.append(course)
    return courses


def records_for_courses(
    repository: CanvasRepository, courses: Iterable[dict[str, Any]]
) -> list[CourseRecord]:
    used = {course.slug for course in repository.courses}
    records: list[CourseRecord] = []
    for course in courses:
        slug = course_slug(course, used)
        used.add(slug)
        records.append(CourseRecord.from_canvas(course, slug=slug))
    return records
