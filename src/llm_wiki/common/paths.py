"""Project paths loaded from config, with repository-layout fallback."""

from __future__ import annotations

import os
import pathlib

import yaml

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[3]
CONFIG_PATH = pathlib.Path(os.environ.get("INGEST_CONFIG", DEFAULT_ROOT / "config.yaml"))


def _config_root_value(data: dict) -> str | None:
    project = data.get("project") or {}
    paths = data.get("paths") or {}
    return project.get("root") or paths.get("root") or data.get("root")


def _load_root_from_config() -> pathlib.Path | None:
    if not CONFIG_PATH.exists():
        return None
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    value = _config_root_value(data)
    if not value:
        return None
    root = pathlib.Path(str(value)).expanduser()
    if not root.is_absolute():
        root = CONFIG_PATH.parent / root
    return root.resolve()


ROOT = _load_root_from_config() or DEFAULT_ROOT
