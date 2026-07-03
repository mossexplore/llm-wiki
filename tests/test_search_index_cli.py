from __future__ import annotations

import pytest

from llm_wiki import search_index


class _FailingBackend:
    def reindex_all(self):
        raise RuntimeError("database down")

    def label(self):
        return "test-db"


def test_search_index_main_exits_cleanly_when_backend_fails(monkeypatch):
    monkeypatch.setattr(search_index, "get_backend", lambda: _FailingBackend())
    monkeypatch.setattr(search_index.sys, "argv", ["search_index", "reindex"])

    with pytest.raises(SystemExit) as exc:
        search_index.main()

    assert str(exc.value) == "检索索引命令执行失败: RuntimeError"
