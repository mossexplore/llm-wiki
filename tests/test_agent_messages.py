from __future__ import annotations

import pytest

from llm_wiki.knowledge import agent


def test_build_answer_messages_compatible_matches_dict_wrapper():
    decision = {"source": "llm", "mode": "none", "context": []}

    messages = agent.build_answer_messages_compatible("怎么排查?", decision)

    assert messages == agent.build_answer_messages("怎么排查?", decision)
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "怎么排查?"}


def test_build_answer_messages_compatible_matches_tuple_wrapper():
    decision = {"source": "llm", "mode": "none", "context": []}

    messages = agent.build_answer_messages_compatible("怎么排查?", decision, message_format="tuple")

    assert messages == agent.build_answer_messages_tuple("怎么排查?", decision)
    assert messages[0][0] == "system"
    assert messages[1] == ("user", "怎么排查?")


def test_build_answer_messages_compatible_keeps_wiki_context_in_both_formats():
    decision = {
        "source": "wiki",
        "mode": "exact",
        "context": [
            {
                "title": "案例标题",
                "file": "wiki/cases/example.md",
                "background": "背景",
                "diagnosis": "定位",
                "solution": "解决",
            }
        ],
    }

    dict_messages = agent.build_answer_messages_compatible("问题", decision)
    tuple_messages = agent.build_answer_messages_compatible("问题", decision, message_format="tuple")

    assert dict_messages[1]["content"] == tuple_messages[1][1]
    assert "案例标题" in dict_messages[1]["content"]
    assert "【用户问题】\n问题" in dict_messages[1]["content"]


def test_message_stats_accepts_dict_and_tuple_messages():
    dict_messages = [{"role": "system", "content": "abc"}, {"role": "user", "content": "de"}]
    tuple_messages = [("system", "abc"), ("user", "de")]

    assert agent.message_stats(dict_messages) == agent.message_stats(tuple_messages)


def test_openai_messages_accepts_dict_and_tuple_messages():
    dict_messages = [{"role": "system", "content": "abc"}, {"role": "user", "content": "de"}]
    tuple_messages = [("system", "abc"), ("user", "de")]

    assert agent.openai_messages(dict_messages) == dict_messages
    assert agent.openai_messages(tuple_messages) == dict_messages


def test_langchain_messages_accepts_dict_and_tuple_messages():
    dict_messages = [{"role": "system", "content": "abc"}, {"role": "user", "content": "de"}]
    tuple_messages = [("system", "abc"), ("user", "de")]

    assert agent.langchain_messages(dict_messages) == tuple_messages
    assert agent.langchain_messages(tuple_messages) == tuple_messages


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_stream_openai_chunks_parses_delta_content():
    chunks = [
        _Obj(choices=[]),
        _Obj(choices=[_Obj(delta=_Obj(content="你"))]),
        {"choices": [{"delta": {"content": "好"}}]},
    ]

    assert list(agent.stream_openai_chunks(chunks, model="test")) == ["你", "好"]


def test_stream_langchain_chunks_parses_content_and_message_content():
    chunks = [
        _Obj(content="你"),
        _Obj(message=_Obj(content="好")),
        {"content": [{"type": "text", "text": "呀"}]},
    ]

    assert list(agent.stream_langchain_chunks(chunks, model="test")) == ["你", "好", "呀"]


def test_build_answer_messages_compatible_rejects_unknown_format():
    with pytest.raises(ValueError):
        agent.build_answer_messages_compatible("问题", {"source": "llm"}, message_format="list")
