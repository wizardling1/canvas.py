from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import requests

from .errors import (
    CanvasAuthenticationError,
    CanvasAuthorizationError,
    CanvasNotFoundError,
    CanvasResponseError,
)

DEFAULT_BASE_URL = "https://wsu.instructure.com"


def parse_links(header: str | None) -> dict[str, str]:
    """Parse the RFC 5988 subset used by Canvas pagination."""
    links: dict[str, str] = {}
    if not header:
        return links
    for part in header.split(","):
        fields = [field.strip() for field in part.split(";")]
        if not fields or not fields[0].startswith("<"):
            continue
        url = fields[0].strip("<>")
        for field in fields[1:]:
            if field.startswith("rel="):
                links[field.removeprefix("rel=").strip('"')] = url
    return links


class CanvasClient:
    """A small, configuration-free client for the Canvas REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        session: requests.Session | None = None,
        timeout: float = 30,
        pagination_delay: float = 0.1,
        max_pages: int = 200,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not token:
            raise ValueError("token is required")
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v1"
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "User-Agent": "canvasapi/0.1",
            }
        )
        self.timeout = timeout
        self.pagination_delay = pagination_delay
        self.max_pages = max_pages

    def resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("https://", "http://")):
            return path_or_url
        if not path_or_url.startswith("/"):
            path_or_url = f"/{path_or_url}"
        if path_or_url.startswith("/api/"):
            return f"{self.base_url}{path_or_url}"
        return f"{self.api_base}{path_or_url}"

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        response = self.session.request(
            method,
            self.resolve_url(path_or_url),
            params=params,
            headers=headers,
            stream=stream,
            timeout=timeout or self.timeout,
            allow_redirects=allow_redirects,
        )
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if response.status_code == 401:
            raise CanvasAuthenticationError("Canvas rejected the access token")
        if response.status_code == 403:
            raise CanvasAuthorizationError(
                "Canvas denied access to this resource"
            )
        if response.status_code == 404:
            raise CanvasNotFoundError("Canvas resource not found")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text[:200] if response.text else ""
            raise CanvasResponseError(
                f"Canvas returned HTTP {response.status_code}: {detail}"
            ) from exc

    def get_json(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        response = self.request("GET", path_or_url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise CanvasResponseError("Canvas returned non-JSON data") from exc

    def iter_pages(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Iterator[Any]:
        url = self.resolve_url(path_or_url)
        first_params = params
        seen: set[str] = set()
        for _ in range(self.max_pages):
            if url in seen:
                raise CanvasResponseError("Canvas pagination loop detected")
            seen.add(url)
            response = self.request("GET", url, params=first_params)
            first_params = None
            try:
                yield response.json()
            except ValueError as exc:
                raise CanvasResponseError("Canvas returned non-JSON data") from exc
            next_url = parse_links(response.headers.get("Link")).get("next")
            if not next_url:
                return
            url = next_url
            if self.pagination_delay:
                time.sleep(self.pagination_delay)
        raise CanvasResponseError(
            f"Canvas pagination exceeded {self.max_pages} pages"
        )

    def iter_items(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        for page in self.iter_pages(path_or_url, params=params):
            if not isinstance(page, list):
                raise CanvasResponseError(
                    f"Expected a list response, got {type(page).__name__}"
                )
            for item in page:
                if isinstance(item, dict):
                    yield item


# Compatibility helpers for the original scripts. They remain configuration-free.
def create_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def iter_paginated(
    session: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    stop_status: tuple[int, ...] = (),
    max_pages: int = 200,
    sleep_seconds: float = 0.1,
) -> Iterable[Any]:
    seen: set[str] = set()
    first_params = params
    for _ in range(max_pages):
        if not url or url in seen:
            return
        seen.add(url)
        response = session.get(url, params=first_params, timeout=30)
        first_params = None
        if response.status_code in stop_status:
            return
        response.raise_for_status()
        yield response.json()
        url = parse_links(response.headers.get("Link")).get("next", "")
        if url and sleep_seconds:
            time.sleep(sleep_seconds)


def get_course_name(
    session: requests.Session,
    course_id: int,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    response = session.get(
        f"{base_url.rstrip('/')}/api/v1/courses/{course_id}", timeout=20
    )
    if response.status_code in (401, 403):
        raise SystemExit(
            f"Unauthorized to access course {course_id}. "
            "Check your Canvas token and permissions."
        )
    if response.status_code == 404:
        raise SystemExit(f"Course {course_id} not found.")
    response.raise_for_status()
    data = response.json()
    return data.get("name") or data.get("course_code") or "(unnamed course)"

