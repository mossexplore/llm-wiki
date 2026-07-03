from __future__ import annotations

import pytest

from llm_wiki.chat_store.mysql_store import _quote_identifier


def test_quote_identifier_wraps_valid_identifier():
    assert _quote_identifier("t_chat_sessions") == "`t_chat_sessions`"


def test_quote_identifier_rejects_invalid_identifier():
    with pytest.raises(ValueError):
        _quote_identifier("t_chat_sessions; DROP TABLE t_chat_messages")
