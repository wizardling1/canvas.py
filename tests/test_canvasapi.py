from __future__ import annotations

from typing import Any

import pytest
import requests

from canvasapi import CanvasAuthenticationError, CanvasClient
from canvasapi.modules import list_modules


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = ""

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def test_client_is_explicitly_configured() -> None:
    session = FakeSession([FakeResponse({"id": 7})])
    client = CanvasClient(
        base_url="https://canvas.example", token="secret", session=session
    )

    assert client.get_json("/users/self/profile") == {"id": 7}
    assert session.headers["Authorization"] == "Bearer secret"
    assert session.requests[0][1] == "https://canvas.example/api/v1/users/self/profile"


def test_authentication_errors_are_typed() -> None:
    client = CanvasClient(
        base_url="https://canvas.example",
        token="bad",
        session=FakeSession([FakeResponse({}, status=401)]),
    )

    with pytest.raises(CanvasAuthenticationError):
        client.get_json("/users/self/profile")


def test_pagination_follows_opaque_next_link() -> None:
    next_url = "https://canvas.example/api/v1/courses?opaque=next"
    session = FakeSession(
        [
            FakeResponse(
                [{"id": 1}], headers={"Link": f'<{next_url}>; rel="next"'}
            ),
            FakeResponse([{"id": 2}]),
        ]
    )
    client = CanvasClient(
        base_url="https://canvas.example",
        token="secret",
        session=session,
        pagination_delay=0,
    )

    assert list(client.iter_items("/courses")) == [{"id": 1}, {"id": 2}]
    assert session.requests[1][1] == next_url
    assert session.requests[1][2]["params"] is None


def test_modules_fall_back_when_inline_items_are_omitted() -> None:
    session = FakeSession(
        [
            FakeResponse([{"id": 10, "name": "Week 1", "items": None}]),
            FakeResponse([{"id": 20, "type": "Page", "title": "Reading"}]),
        ]
    )
    client = CanvasClient(
        base_url="https://canvas.example",
        token="secret",
        session=session,
        pagination_delay=0,
    )

    modules = list_modules(client, 99)

    assert modules[0]["items"][0]["title"] == "Reading"
    assert session.requests[1][1].endswith("/courses/99/modules/10/items")

