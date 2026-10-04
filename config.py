from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "FrameForge Studio"
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_WORK_FPS = 12
DEFAULT_FINAL_FPS = 24


def app_data_dir() -> Path:
    """Return a user-writable settings directory on all supported platforms."""
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home()))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = root / "FrameForgeStudio"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        # Frozen apps can run in a restricted profile/container where HOME is
        # present but its config parent is not. Keep settings functional rather
        # than failing before the main window appears.
        fallback = Path(tempfile.gettempdir()) / "FrameForgeStudio"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def load_settings() -> dict[str, Any]:
    path = app_data_dir() / "settings.json"
    if not path.exists():
        return {
            "theme": "dark",
            "ffmpeg_path": "ffmpeg",
            "autosave_seconds": 30,
            "thumbnail_size": 160,
            "default_work_fps": DEFAULT_WORK_FPS,
            "default_final_fps": DEFAULT_FINAL_FPS,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict[str, Any]) -> None:
    path = app_data_dir() / "settings.json"
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
