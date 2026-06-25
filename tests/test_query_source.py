from __future__ import annotations

from llm_wiki.knowledge import query


class _BackendUnavailable:
    def search(self, _log):
        return None


def test_query_marks_none_source_when_backend_unavailable_and_local_search_disabled(monkeypatch):
    monkeypatch.setattr(query.search_index, "get_backend", lambda: _BackendUnavailable())
    monkeypatch.setattr(query.storage_config, "local_search", lambda: False)

    res = query.search("anything")

    assert res["mode"] == "none"
    assert res["source"] == "none"
    assert res["hits"] == []


def test_query_marks_files_source_when_falling_back_to_local_search(tmp_path, monkeypatch):
    root = tmp_path
    cases_dir = root / "wiki" / "cases"
    cases_dir.mkdir(parents=True)
    (cases_dir / "hikari.md").write_text(
        """---
title: HikariPool 连接池耗尽
status: verified
confidence: high
signatures:
  - HikariPool-1 - Connection is not available
---

## 解决方案
调大连接池并优化慢查询。
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(query.search_index, "get_backend", lambda: _BackendUnavailable())
    monkeypatch.setattr(query.storage_config, "local_search", lambda: True)
    monkeypatch.setattr(query, "ROOT", root)
    monkeypatch.setattr(query, "CASES_DIR", cases_dir)

    res = query.search("ERROR HikariPool-1 - Connection is not available")

    assert res["mode"] == "exact"
    assert res["source"] == "files"
    assert res["hits"][0]["file"] == "wiki/cases/hikari.md"
