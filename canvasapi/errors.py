from __future__ import annotations


class CanvasError(RuntimeError):
    """Base error raised by canvasapi."""


class CanvasAuthenticationError(CanvasError):
    """The access token was not accepted by Canvas."""


class CanvasAuthorizationError(CanvasError):
    """The authenticated user cannot access a Canvas resource."""


class CanvasNotFoundError(CanvasError):
    """A Canvas resource does not exist or is not visible to the caller."""


class CanvasResponseError(CanvasError):
    """Canvas returned an unexpected response."""

