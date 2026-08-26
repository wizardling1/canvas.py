from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict, Optional


class CanvasConfig(TypedDict, total=False):
    token: str
    course_id: int
    base_url: str


def load_config_from_cwd(path: Optional[Path] = None) -> CanvasConfig:
    """
    Load canvas_config.json from the current working directory.
    If CANVAS_TOKEN is set, prefer it over file token.
    """
    cfg_path = (path or Path.cwd()) / "canvas_config.json"
    cfg: CanvasConfig = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            if isinstance(data, dict):
                cfg.update(data)  # type: ignore[arg-type]
        except json.JSONDecodeError as e:
            raise SystemExit(f"Invalid JSON in {cfg_path}: {e}")
    else:
        # File missing is allowed if env var provides token; caller may still require course_id
        pass

    env_token = os.getenv("CANVAS_TOKEN")
    if env_token:
        cfg["token"] = env_token

    return cfg


def save_config(config: CanvasConfig, path: Optional[Path] = None) -> Path:
    """Atomically save CLI-owned configuration in the current directory."""
    cfg_path = (path or Path.cwd()) / "canvas_config.json"
    temporary = cfg_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    temporary.replace(cfg_path)
    return cfg_path
