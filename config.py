#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


class ConfigError(ValueError):
    pass


@dataclass(slots=True)
class LauncherConfig:
    project_root: Path
    config_path: Path
    library_root: Path
    controller_script: Path
    bridge_log_path: Path
    window_title: str
    window_width: int
    window_height: int
    fullscreen: bool
    production_mode: bool
    enable_controller_bridge: bool
    java_command: str
    grid_columns: int
    show_mouse_cursor: bool
    python_command: str

    @property
    def window_size(self) -> tuple[int, int]:
        return (self.window_width, self.window_height)

    @property
    def is_linux(self) -> bool:
        return sys.platform.startswith("linux")

    @property
    def is_windows(self) -> bool:
        return sys.platform == "win32"


def load_config(
    config_path: Path | None = None,
    *,
    library_root_override: str | None = None,
    windowed: bool = False,
    production_override: bool | None = None,
) -> LauncherConfig:
    resolved_config_path = _resolve_config_path(config_path)
    file_data = _read_config_file(resolved_config_path)

    fullscreen_default = sys.platform != "win32"

    fullscreen = _coerce_bool(
        os.getenv("ARCADE_FULLSCREEN"),
        _coerce_bool(file_data.get("fullscreen"), fullscreen_default),
    )
    if windowed:
        fullscreen = False

    production_mode = _coerce_bool(
        os.getenv("ARCADE_PRODUCTION"),
        _coerce_bool(file_data.get("production_mode"), False),
    )
    if production_override is not None:
        production_mode = production_override

    library_root_value = (
        library_root_override
        or os.getenv("ARCADE_LIBRARY_ROOT")
        or file_data.get("library_root")
        or "games"
    )

    show_mouse_cursor = _coerce_bool(
        os.getenv("ARCADE_SHOW_CURSOR"),
        _coerce_bool(file_data.get("show_mouse_cursor"), not fullscreen),
    )

    config = LauncherConfig(
        project_root=PROJECT_ROOT,
        config_path=resolved_config_path,
        library_root=_resolve_project_path(library_root_value),
        controller_script=_resolve_project_path(
            file_data.get("controller_script") or "controller_to_keyboard.py"
        ),
        bridge_log_path=_resolve_project_path(
            file_data.get("bridge_log_path") or "logs/controller_bridge.log"
        ),
        window_title=str(file_data.get("window_title") or "Arcade Machine"),
        window_width=max(
            960,
            _coerce_int(
                os.getenv("ARCADE_WINDOW_WIDTH"),
                _coerce_int(file_data.get("window_width"), 1440),
            ),
        ),
        window_height=max(
            540,
            _coerce_int(
                os.getenv("ARCADE_WINDOW_HEIGHT"),
                _coerce_int(file_data.get("window_height"), 900),
            ),
        ),
        fullscreen=fullscreen,
        production_mode=production_mode,
        enable_controller_bridge=_coerce_bool(
            os.getenv("ARCADE_ENABLE_CONTROLLER_BRIDGE"),
            _coerce_bool(file_data.get("enable_controller_bridge"), True),
        ),
        java_command=str(os.getenv("ARCADE_JAVA_COMMAND") or file_data.get("java_command") or "java"),
        grid_columns=max(1, min(6, _coerce_int(file_data.get("grid_columns"), 3))),
        show_mouse_cursor=show_mouse_cursor,
        python_command=sys.executable,
    )

    return config


def prepare_runtime_paths(config: LauncherConfig) -> None:
    config.library_root.mkdir(parents=True, exist_ok=True)
    config.bridge_log_path.parent.mkdir(parents=True, exist_ok=True)


def _resolve_config_path(config_path: Path | None) -> Path:
    if config_path is None:
        return DEFAULT_CONFIG_PATH
    if config_path.is_absolute():
        return config_path
    return Path.cwd() / config_path


def _read_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Config file {config_path} must contain a JSON object.")

    return payload


def _resolve_project_path(raw_path: str | os.PathLike[str]) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"Cannot interpret {value!r} as a boolean value.")


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ConfigError(f"Cannot interpret {value!r} as an integer value.") from exc
    raise ConfigError(f"Cannot interpret {value!r} as an integer value.")