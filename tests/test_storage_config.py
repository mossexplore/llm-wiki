from __future__ import annotations

from llm_wiki.common import storage_config


def test_auto_reindex_invalid_bool_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        storage_config,
        "_config_data",
        lambda: {"storage": {"auto_reindex_on_startup": "not-a-bool"}},
    )
    monkeypatch.delenv("LOG_WIKI_AUTO_REINDEX_ON_STARTUP", raising=False)

    assert storage_config.auto_reindex_on_startup() is False


def test_local_search_invalid_bool_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(storage_config, "_config_data", lambda: {"storage": {"local_search": "maybe"}})
    monkeypatch.delenv("LOG_WIKI_LOCAL_SEARCH", raising=False)

    assert storage_config.local_search() is True
