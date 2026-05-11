from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_WORKFLOW_DIR = "./workflows"
DEFAULT_REFRESH_INTERVAL = 1.0


@dataclass(frozen=True)
class AppConfig:
    workflow_dir: Path
    refresh_interval: float
    messages: list[str] = field(default_factory=list)
    comfyui_dir: Path | None = None


def load_config(cwd: Path | None = None) -> AppConfig:
    cwd = cwd or Path.cwd()
    config_path = cwd / "comfy-helper.yaml"
    messages: list[str] = []
    raw: dict[str, Any] = {}

    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
            elif loaded is not None:
                messages.append("Invalid config root; using defaults for all fields.")
        except Exception as exc:
            messages.append("Failed to parse comfy-helper.yaml; using defaults.")
            import logging

            logging.exception("Failed to parse config file: %s", exc)

    workflow_dir_value = raw.get("workflow_dir", DEFAULT_WORKFLOW_DIR)
    if not isinstance(workflow_dir_value, str) or not workflow_dir_value.strip():
        messages.append("Invalid workflow_dir; using default ./workflows.")
        workflow_dir_value = DEFAULT_WORKFLOW_DIR

    refresh_value = raw.get("refresh_interval", DEFAULT_REFRESH_INTERVAL)
    try:
        refresh_interval = float(refresh_value)
    except (TypeError, ValueError):
        messages.append("Invalid refresh_interval; using default 1.0.")
        refresh_interval = DEFAULT_REFRESH_INTERVAL

    comfyui_dir_value = raw.get("comfyui_dir")
    comfyui_dir: Path | None = None
    if comfyui_dir_value is not None:
        if isinstance(comfyui_dir_value, str) and comfyui_dir_value.strip():
            comfyui_path = Path(comfyui_dir_value)
            comfyui_dir = (
                (cwd / comfyui_path).resolve()
                if not comfyui_path.is_absolute()
                else comfyui_path.resolve()
            )
            if not comfyui_dir.exists() or not comfyui_dir.is_dir():
                messages.append("Invalid comfyui_dir; ignoring local ComfyUI copy mode.")
                comfyui_dir = None
        else:
            messages.append("Invalid comfyui_dir; ignoring local ComfyUI copy mode.")

    return AppConfig(
        workflow_dir=(cwd / workflow_dir_value).resolve()
        if not Path(workflow_dir_value).is_absolute()
        else Path(workflow_dir_value),
        refresh_interval=refresh_interval,
        comfyui_dir=comfyui_dir,
        messages=messages,
    )
