#!/usr/bin/env python3
"""
agent.py — 对话 Agent 的「先检索、后生成」编排层(RAG)

回答策略(对应需求):
  1) 先用 query.search() 检索本地知识库:
     - 精确命中(mode=exact):signature 原文匹配。关联度最高。
     - 模糊命中且关联度足够大(mode=fuzzy 且 top score >= 阈值)。
     命中时:把检索到的案例资料(背景/定位/解决方案)+ 自定义提示词一起喂给大模型,
     由大模型基于这些资料**流式**生成回答,并标注来源 wiki。
  2) 没有相关案例(mode=none),或模糊命中但关联度太小(score < 阈值)→ 不带知识库资料,
     直接让大模型基于通用经验**流式**回答。

两条路径都走 OpenAI 兼容接口的真流式;区别只在于「是否把检索到的 wiki 资料注入上下文」。

检索决策(retrieve)已拆到 retriever.py;本模块只负责「拿到决策后怎么生成」。

可调项(环境变量):
  - CHAT_WIKI_PROMPT      命中知识库时的自定义系统提示词(覆盖默认 RAG 提示词)。
  - CHAT_SYSTEM_PROMPT    未命中、纯大模型兜底时的系统提示词。
"""

from __future__ import annotations

import logging
import os
import time

from llm_wiki.common.llm import client_and_model, load_config

logger = logging.getLogger("log_wiki.agent")

# 命中知识库时的自定义提示词:让大模型「基于检索到的 wiki 资料」作答(RAG)。
DEFAULT_WIKI_PROMPT = (
    "你是 llm-wiki 的运维排查助手。下面会给你一段「知识库检索到的相关案例资料」(来源 wiki),"
    "以及用户的问题。请**优先依据这些资料**回答:整理出有条理、可直接落地的解决方案与排查步骤。"
    "资料已覆盖的内容要忠于资料,不要改写其中的关键结论、错误原文或参数;"
    "资料未覆盖、但用户确实需要的部分,可以补充通用工程经验,并说明这是补充建议而非知识库结论。"
    "严禁编造资料里不存在的日志、错误码或结果。请用中文回答,语言简洁专业。"
)
WIKI_PROMPT = os.environ.get("CHAT_WIKI_PROMPT") or DEFAULT_WIKI_PROMPT

# 未命中时的兜底提示词:纯大模型回答。
DEFAULT_CHAT_PROMPT = (
    "你是 llm-wiki 的运维排查助手。本地知识库里没有检索到相关案例,所以下面由你来回答。"
    "请基于通用的工程与运维经验,给出有条理、可操作的排查建议;"
    "不确定时要诚实说明,不要编造不存在的日志、错误码或结论。请用中文回答。"
)
CHAT_SYSTEM_PROMPT = os.environ.get("CHAT_SYSTEM_PROMPT") or DEFAULT_CHAT_PROMPT


def _context_block(context: list, related: bool) -> str:
    """把检索到的案例拼成喂给大模型的资料文本。"""
    head = (
        "【知识库检索到的相关案例资料(关联度较高,供参考)】"
        if related
        else "【知识库检索到的相关案例资料(精确命中)】"
    )
    blocks = [head, ""]
    for i, c in enumerate(context, 1):
        blocks.append(f"案例{i}:{c['title']}(来源 wiki:{c['file']})")
        if c.get("background"):
            blocks.append(f"问题背景:{c['background']}")
        if c.get("diagnosis"):
            blocks.append(f"定位过程:{c['diagnosis']}")
        if c.get("solution"):
            blocks.append(f"解决方案:{c['solution']}")
        blocks.append("")
    return "\n".join(blocks).strip()


def _message_role_content(message: dict | tuple) -> tuple[str, str]:
    """兼容 dict/tuple 两种 message 结构,统一取出 role 与 content。"""
    if isinstance(message, dict):
        return message.get("role", ""), message.get("content") or ""
    if isinstance(message, tuple) and len(message) >= 2:
        return message[0] or "", message[1] or ""
    return "", ""


def message_stats(messages: list[dict | tuple]) -> dict:
    lengths = [
        {"role": role, "chars": len(content)}
        for role, content in (_message_role_content(message) for message in messages)
    ]
    return {
        "message_count": len(messages),
        "char_count": sum(item["chars"] for item in lengths),
        "message_lengths": lengths,
    }


def openai_messages(messages: list[dict | tuple]) -> list[dict]:
    """把 dict/tuple message 统一转成 OpenAI SDK 需要的 dict 结构。"""
    return [
        {"role": role, "content": content}
        for role, content in (_message_role_content(message) for message in messages)
    ]


def langchain_messages(messages: list[dict | tuple]) -> list[tuple]:
    """把 dict/tuple message 统一转成 LangChain 可接受的 tuple 结构。"""
    return [(role, content) for role, content in (_message_role_content(message) for message in messages)]


def _bool_config(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on", "enabled", "enable"):
            return True
        if v in ("0", "false", "no", "n", "off", "disabled", "disable"):
            return False
    return default


def _chat_thinking_enabled() -> bool:
    """读取 chat 段 Thinking 开关;默认启用,禁用时才下发 provider 扩展参数。"""
    cfg = load_config("chat")
    value = cfg.get("thinking", cfg.get("think", cfg.get("enable_thinking")))
    return _bool_config(value, default=True)


def _build_answer_message_pairs(text: str, decision: dict) -> list[tuple]:
    """构造本轮 message 的共享语义结构。"""
    messages = [("system", WIKI_PROMPT if decision.get("source") == "wiki" else CHAT_SYSTEM_PROMPT)]
    if decision.get("source") == "wiki":
        context_text = _context_block(decision.get("context", []), related=(decision.get("mode") == "fuzzy"))
        messages.append(("user", f"{context_text}\n\n【用户问题】\n{text}"))
    else:
        messages.append(("user", text))
    return messages


def build_answer_messages_compatible(
    text: str, decision: dict, message_format: str = "dict"
) -> list[dict] | list[tuple]:
    """构造本轮发给大模型的 messages,兼容 dict 与 tuple 两种返回格式。

    对话页面不做多轮上下文注入:每次请求只携带系统提示和本轮用户问题。
    """
    pairs = _build_answer_message_pairs(text, decision)
    if message_format == "dict":
        return [{"role": role, "content": content} for role, content in pairs]
    if message_format == "tuple":
        return pairs
    raise ValueError("message_format must be 'dict' or 'tuple'")


def build_answer_messages(text: str, decision: dict) -> list[dict]:
    """构造本轮发给大模型的 messages。

    对话页面不做多轮上下文注入:每次请求只携带系统提示和本轮用户问题。
    """
    return build_answer_messages_compatible(text, decision, message_format="dict")


def build_answer_messages_tuple(text: str, decision: dict) -> list[tuple]:
    """构造本轮发给大模型的 messages。

    对话页面不做多轮上下文注入:每次请求只携带系统提示和本轮用户问题。
    """
    return build_answer_messages_compatible(text, decision, message_format="tuple")


def _value(obj, key: str):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _content_to_text(content) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _openai_chunk_content(chunk) -> str:
    choices = _value(chunk, "choices") or []
    if not choices:
        return ""
    delta = _value(choices[0], "delta")
    return _content_to_text(_value(delta, "content")) if delta else ""


def _langchain_chunk_content(chunk) -> str:
    content = _value(chunk, "content")
    if content is None:
        message = _value(chunk, "message")
        content = _value(message, "content") if message else None
    if content is None:
        content = _value(chunk, "text")
    return _content_to_text(content)


def _stream_chunks(chunks, model: str, started: float, parser, parser_name: str):
    first_chunk_logged = False
    first_content_logged = False
    for chunk in chunks:
        if not first_chunk_logged:
            first_chunk_logged = True
            logger.info(
                f"agent.chat.first_chunk parser={parser_name} model={model} "
                f"first_chunk_ms={int((time.perf_counter() - started) * 1000)}"
            )
        content = parser(chunk)
        if content:
            if not first_content_logged:
                first_content_logged = True
                logger.info(
                    f"agent.chat.first_content parser={parser_name} model={model} "
                    f"first_content_ms={int((time.perf_counter() - started) * 1000)}"
                )
            yield content


def stream_openai_chunks(chunks, model: str = "", started: float | None = None):
    """解析 OpenAI SDK 流式响应 chunk,逐段 yield 文本。"""
    yield from _stream_chunks(chunks, model, started or time.perf_counter(), _openai_chunk_content, "openai")


def stream_langchain_chunks(chunks, model: str = "", started: float | None = None):
    """解析 LangChain 流式响应 chunk,逐段 yield 文本。"""
    yield from _stream_chunks(
        chunks, model, started or time.perf_counter(), _langchain_chunk_content, "langchain"
    )


def stream_messages(messages):
    """统一的大模型流式调用:逐段 yield 文本增量。对话用 config.yaml 的 chat 段(可与写入不同)。"""
    client, model = client_and_model("chat")
    thinking_enabled = _chat_thinking_enabled()
    stats = message_stats(messages)
    started = time.perf_counter()
    logger.info(
        f"agent.chat.request model={model} thinking_enabled={thinking_enabled} "
        f"message_count={stats['message_count']} char_count={stats['char_count']} "
        f"message_lengths={stats['message_lengths']}"
    )
    request_kwargs = {
        "model": model,
        "temperature": 0.3,
        "messages": openai_messages(messages),
        "stream": True,
    }
    if not thinking_enabled:
        request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    stream = client.chat.completions.create(**request_kwargs)
    logger.info(
        f"agent.chat.stream_created model={model} create_ms={int((time.perf_counter() - started) * 1000)}"
    )
    yield from stream_openai_chunks(stream, model=model, started=started)


def stream_messages_langchain(messages):
    """用 LangChain ChatOpenAI 流式调用,并按 LangChain chunk 结构解析文本增量。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("使用 message_format=langchain 需要安装 langchain-openai。") from exc

    cfg = load_config("chat")
    model = cfg.get("model", "gpt-4o")
    thinking_enabled = _chat_thinking_enabled()
    stats = message_stats(messages)
    started = time.perf_counter()
    logger.info(
        f"agent.chat.request parser=langchain model={model} thinking_enabled={thinking_enabled} "
        f"message_count={stats['message_count']} char_count={stats['char_count']} "
        f"message_lengths={stats['message_lengths']}"
    )
    llm_kwargs = {
        "model": model,
        "temperature": 0.3,
        "api_key": cfg["api_key"],
    }
    if cfg.get("base_url"):
        llm_kwargs["base_url"] = cfg["base_url"]
    if not thinking_enabled:
        llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    chat_model = ChatOpenAI(**llm_kwargs)
    stream = chat_model.stream(langchain_messages(messages))
    logger.info(
        f"agent.chat.stream_created parser=langchain model={model} "
        f"create_ms={int((time.perf_counter() - started) * 1000)}"
    )
    yield from stream_langchain_chunks(stream, model=model, started=started)


def stream_messages_compatible(messages, message_format: str = "dict"):
    """按消息格式选择对应的流式调用与响应解析。"""
    if message_format == "tuple":
        yield from stream_messages_langchain(messages)
    elif message_format == "dict":
        yield from stream_messages(messages)
    else:
        raise ValueError("message_format must be 'dict' or 'tuple'")
