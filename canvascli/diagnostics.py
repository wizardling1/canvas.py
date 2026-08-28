from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CLIError(Exception):
    """An expected command-line failure with an optional recovery hint."""

    message: str
    hint: str | None = None
    exit_code: int = 2

    def __str__(self) -> str:
        return self.message


def render_error(error: CLIError) -> str:
    lines = [f"canvascli: error: {error.message}"]
    if error.hint:
        lines.append(f"canvascli: hint: {error.hint}")
    return "\n".join(lines)
