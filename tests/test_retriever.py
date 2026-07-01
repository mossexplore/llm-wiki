from __future__ import annotations

from llm_wiki.chat.retriever import WikiRetriever
from llm_wiki.knowledge import query


def test_wiki_retriever_reads_contexts_from_query_backend(monkeypatch):
    captured = {}

    def fake_search(_text):
        return {
            "mode": "exact",
            "source": "mysql",
            "elapsed_ms": 7,
            "hits": [
                {"file": "wiki/cases/db.md", "title": "数据库案例"},
                {"file": "wiki/cases/other.md", "title": "其他案例"},
            ],
        }

    def fake_get_contexts(files):
        captured["files"] = files
        return [
            {
                "title": "数据库案例",
                "file": "wiki/cases/db.md",
                "background": "背景来自数据库",
                "diagnosis": "定位来自数据库",
                "solution": "方案来自数据库",
            }
        ]

    monkeypatch.setattr(query, "search", fake_search)
    monkeypatch.setattr(query, "get_contexts", fake_get_contexts)

    decision = WikiRetriever().retrieve("报错")

    assert captured["files"] == ["wiki/cases/db.md", "wiki/cases/other.md"]
    assert decision["source"] == "wiki"
    assert decision["mode"] == "exact"
    assert decision["elapsed_ms"] == 7
    assert decision["refs"] == [{"file": "wiki/cases/db.md", "title": "数据库案例"}]
    assert decision["context"][0]["solution"] == "方案来自数据库"


def test_wiki_retriever_falls_back_to_llm_when_database_context_missing(monkeypatch):
    monkeypatch.setattr(
        query,
        "search",
        lambda _text: {
            "mode": "exact",
            "source": "files",
            "elapsed_ms": 3,
            "hits": [{"file": "wiki/cases/local-only.md", "title": "本地文件命中"}],
        },
    )
    monkeypatch.setattr(query, "get_contexts", lambda _files: [])

    decision = WikiRetriever().retrieve("报错")

    assert decision == {"source": "llm", "mode": "none", "elapsed_ms": 3, "refs": [], "context": []}
