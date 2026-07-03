from __future__ import annotations

from llm_wiki.backend.core.middleware import _redact_query_string


def test_redact_query_string_masks_sensitive_values():
    query = "q=hello&token=abc123&password=pwd&empty=&page=1"

    redacted = _redact_query_string(query)

    assert "q=hello" in redacted
    assert "page=1" in redacted
    assert "empty=" in redacted
    assert "token=%2A%2A%2A" in redacted
    assert "password=%2A%2A%2A" in redacted
    assert "abc123" not in redacted
    assert "pwd" not in redacted


def test_redact_query_string_matches_sensitive_keys_case_insensitively():
    redacted = _redact_query_string("Access_Token=abc&Authorization=bearer")

    assert "abc" not in redacted
    assert "bearer" not in redacted
    assert redacted.count("%2A%2A%2A") == 2
