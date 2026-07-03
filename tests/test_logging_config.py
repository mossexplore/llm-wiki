from __future__ import annotations

import logging
import pathlib
from logging.handlers import RotatingFileHandler

from llm_wiki.common import logging_config


def test_setup_logging_falls_back_when_log_dir_cannot_be_created(monkeypatch, tmp_path):
    bad_dir = tmp_path / "bad"
    fallback_dir = tmp_path / "fallback"
    calls = []
    original_mkdir = pathlib.Path.mkdir

    def fake_mkdir(self, *args, **kwargs):
        calls.append(self)
        if self == bad_dir:
            raise OSError("permission denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setenv("LOG_WIKI_LOG_DIR", str(bad_dir))
    monkeypatch.setattr(logging_config, "FALLBACK_LOG_DIR", fallback_dir)
    monkeypatch.setattr(pathlib.Path, "mkdir", fake_mkdir)

    log_dir = logging_config.setup_logging()

    assert log_dir == fallback_dir
    assert bad_dir in calls
    assert fallback_dir.exists()


def test_setup_logging_compares_resolved_handler_paths(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("LOG_WIKI_LOG_DIR", str(log_dir))

    root = logging.getLogger()
    access = logging.getLogger("log_wiki.access")
    old_root_handlers = list(root.handlers)
    old_access_handlers = list(access.handlers)
    try:
        root.handlers = []
        access.handlers = []

        logging_config.setup_logging()
        logging_config.setup_logging()

        root_files = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        access_files = [h for h in access.handlers if isinstance(h, RotatingFileHandler)]
        assert len(root_files) == 1
        assert len(access_files) == 1
        assert root_files[0].baseFilename == str((log_dir / "app.log").resolve())
        assert access_files[0].baseFilename == str((log_dir / "access.log").resolve())
    finally:
        for handler in root.handlers + access.handlers:
            handler.close()
        root.handlers = old_root_handlers
        access.handlers = old_access_handlers
