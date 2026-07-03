"""Project paths loaded from config, with repository-layout fallback."""

from __future__ import annotations

import os
import pathlib

import yaml

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[3]
CONFIG_PATH = pathlib.Path(os.environ.get("INGEST_CONFIG", DEFAULT_ROOT / "config.yaml"))


def _existing_root(path: pathlib.Path, source: str) -> pathlib.Path:
    root = path.resolve()
    if not root.exists():
        raise RuntimeError(f"{source} 不存在: {root}")
    if not root.is_dir():
        raise RuntimeError(f"{source} 不是目录: {root}")
    return root


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
    return _existing_root(root, "配置的项目根目录")


ROOT = _load_root_from_config() or _existing_root(DEFAULT_ROOT, "默认项目根目录")
