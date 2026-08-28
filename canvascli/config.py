from __future__ import annotations

import json
import os
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path
from typing import TypedDict, Optional


class CanvasConfig(TypedDict, total=False):
    token: str
    course_id: int
    base_url: str


def load_config_from_cwd(path: Optional[Path] = None) -> CanvasConfig:
    """Load non-secret settings from TOML and credentials from the environment."""
    cfg_path = (path or Path.cwd()) / "canvascli.toml"
    cfg: CanvasConfig = {}
    if cfg_path.exists():
        try:
            with cfg_path.open("rb") as config_file:
                data = tomllib.load(config_file)
            if data.get("course_id") is not None:
                cfg["course_id"] = int(data["course_id"])
            if data.get("base_url") is not None:
                cfg["base_url"] = str(data["base_url"])
        except (tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
            raise SystemExit(f"Invalid TOML in {cfg_path}: {exc}") from exc
    else:
        # File missing is allowed if env var provides token; caller may still require course_id
        pass

    env_token = os.getenv("CANVAS_TOKEN")
    if env_token:
        cfg["token"] = env_token

    return cfg


def save_config(config: CanvasConfig, path: Optional[Path] = None) -> Path:
    """Atomically save only non-secret CLI-owned configuration."""
    cfg_path = (path or Path.cwd()) / "canvascli.toml"
    temporary = cfg_path.with_suffix(".toml.tmp")
    lines: list[str] = []
    if "base_url" in config:
        lines.append(f"base_url = {json.dumps(str(config['base_url']))}")
    if "course_id" in config:
        lines.append(f"course_id = {int(config['course_id'])}")
    temporary.write_text("\n".join(lines) + "\n")
    temporary.replace(cfg_path)
    return cfg_path
