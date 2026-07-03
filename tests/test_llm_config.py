from __future__ import annotations

from llm_wiki.common import llm


def test_load_config_preserves_false_zero_and_empty_string_overrides(monkeypatch):
    monkeypatch.setattr(
        llm,
        "_load_config_data",
        lambda: {
            "openai": {
                "api_key": "base-key",
                "base_url": "https://example.test",
                "model": "base-model",
                "thinking": True,
                "timeout": 30,
            },
            "chat": {
                "base_url": "",
                "model": "chat-model",
                "thinking": False,
                "timeout": 0,
                "ignored": None,
            },
        },
    )

    cfg = llm.load_config("chat")

    assert cfg["api_key"] == "base-key"
    assert cfg["base_url"] == ""
    assert cfg["model"] == "chat-model"
    assert cfg["thinking"] is False
    assert cfg["timeout"] == 0
    assert "ignored" not in cfg
