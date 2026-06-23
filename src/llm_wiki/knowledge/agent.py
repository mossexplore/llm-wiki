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

可调项(环境变量):
  - CHAT_FUZZY_THRESHOLD  模糊命中判为「关联度大」的最低分,默认 1.0(bm25 取负,越大越相关)。
  - CHAT_WIKI_PROMPT      命中知识库时的自定义系统提示词(覆盖默认 RAG 提示词)。
  - CHAT_SYSTEM_PROMPT    未命中、纯大模型兜底时的系统提示词。
"""
from __future__ import annotations
import os, re, logging

from llm_wiki.common.paths import ROOT

from . import ingest, query

CASES_DIR = ROOT / "wiki" / "cases"
logger = logging.getLogger("log_wiki.agent")

# 模糊命中判定为「关联度大」的最低分(bm25 取负后,越大越相关)。可用环境变量覆盖。
FUZZY_THRESHOLD = float(os.environ.get("CHAT_FUZZY_THRESHOLD", "1.0"))

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


def _section(body: str, title: str) -> str:
    m = re.search(rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    return m.group(1).strip() if m else ""


def _case_context(file_rel: str) -> dict | None:
    """读 wiki/cases/<file>,取标题与各正文段落,作为 RAG 上下文。"""
    path = (ROOT / file_rel).resolve()
    try:
        path.relative_to(CASES_DIR.resolve())
    except ValueError:
        return None
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    title = path.stem
    m = re.search(r"^title:[ \t]*(.+)$", text, re.M)
    if m:
        title = m.group(1).strip().strip('"\'')
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    return {
        "title": title,
        "file": file_rel,
        "background": _section(body, "问题背景"),
        "diagnosis": _section(body, "定位过程"),
        "solution": _section(body, "解决方案"),
    }


def retrieve(text: str) -> dict:
    """决定本轮怎么答:返回决策字典。

    {
      "source":  "wiki" | "llm",
      "mode":    "exact" | "fuzzy" | "none",
      "elapsed_ms": int,                 # 检索耗时
      "refs":    [{"file","title"}],     # source=wiki 时引用的案例(给前端展示)
      "context": [{"title","file","background","diagnosis","solution"}],  # 注入大模型的资料
    }
    """
    res = query.search(text)
    mode = res.get("mode", "none")
    hits = res.get("hits", []) or []
    elapsed = res.get("elapsed_ms", 0)

    picked_files: list[dict] = []
    if mode == "exact" and hits:
        picked_files = [{"file": h["file"], "title": h.get("title", "")} for h in hits[:3]]
    elif mode == "fuzzy" and hits:
        top = hits[0]
        if isinstance(top.get("score"), (int, float)) and top["score"] >= FUZZY_THRESHOLD:
            for h in hits[:2]:
                if isinstance(h.get("score"), (int, float)) and h["score"] < FUZZY_THRESHOLD:
                    continue
                picked_files.append({"file": h["file"], "title": h.get("title", "")})

    if picked_files:
        context = [c for c in (_case_context(p["file"]) for p in picked_files) if c]
        if context:
            refs = [{"file": c["file"], "title": c["title"]} for c in context]
            return {"source": "wiki", "mode": mode, "elapsed_ms": elapsed,
                    "refs": refs, "context": context}

    # 关联度小 / 无命中 → 交给大模型(不带知识库资料)
    return {"source": "llm", "mode": mode, "elapsed_ms": elapsed, "refs": [], "context": []}


def _context_block(context: list, related: bool) -> str:
    """把检索到的案例拼成喂给大模型的资料文本。"""
    head = ("【知识库检索到的相关案例资料(关联度较高,供参考)】"
            if related else "【知识库检索到的相关案例资料(精确命中)】")
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


def message_stats(messages: list[dict]) -> dict:
    lengths = [{"role": m.get("role", ""), "chars": len(m.get("content") or "")} for m in messages]
    return {
        "message_count": len(messages),
        "char_count": sum(item["chars"] for item in lengths),
        "message_lengths": lengths,
        "history_messages": max(0, len(messages) - 2),
    }


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
    cfg = ingest.load_config("chat")
    value = cfg.get("thinking", cfg.get("think", cfg.get("enable_thinking")))
    return _bool_config(value, default=True)


def build_answer_messages(text: str, history: list | None, decision: dict) -> list[dict]:
    """构造本轮发给大模型的 messages。

    对话页面不做多轮上下文注入:每次请求只携带系统提示和本轮用户问题。
    history 参数保留在函数签名中,兼容旧调用点,但不再写入 messages。
    """
    messages = [{"role": "system", "content": WIKI_PROMPT if decision.get("source") == "wiki" else CHAT_SYSTEM_PROMPT}]
    if decision.get("source") == "wiki":
        context_text = _context_block(decision.get("context", []), related=(decision.get("mode") == "fuzzy"))
        messages.append({"role": "user", "content": f"{context_text}\n\n【用户问题】\n{text}"})
    else:
        messages.append({"role": "user", "content": text})
    return messages


def stream_messages(messages):
    """统一的大模型流式调用:逐段 yield 文本增量。对话用 config.yaml 的 chat 段(可与写入不同)。"""
    import time
    client, model = ingest._client_and_model("chat")
    thinking_enabled = _chat_thinking_enabled()
    stats = message_stats(messages)
    started = time.perf_counter()
    logger.info(
        "agent.chat.request model=%s thinking_enabled=%s message_count=%s char_count=%s history_messages=%s message_lengths=%s",
        model, thinking_enabled, stats["message_count"], stats["char_count"],
        stats["history_messages"], stats["message_lengths"],
    )
    request_kwargs = {
        "model": model,
        "temperature": 0.3,
        "messages": messages,
        "stream": True,
    }
    if not thinking_enabled:
        request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    stream = client.chat.completions.create(**request_kwargs)
    logger.info(
        "agent.chat.stream_created model=%s create_ms=%s",
        model, int((time.perf_counter() - started) * 1000),
    )
    first_chunk_logged = False
    first_content_logged = False
    for chunk in stream:
        if not first_chunk_logged:
            first_chunk_logged = True
            logger.info(
                "agent.chat.first_chunk model=%s first_chunk_ms=%s",
                model, int((time.perf_counter() - started) * 1000),
            )
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            if not first_content_logged:
                first_content_logged = True
                logger.info(
                    "agent.chat.first_content model=%s first_content_ms=%s",
                    model, int((time.perf_counter() - started) * 1000),
                )
            yield chunk.choices[0].delta.content


def _stream_chat(messages):
    """兼容旧调用名;新代码优先用 stream_messages(),这样语义更明确。"""
    yield from stream_messages(messages)


def _history_messages(history: list | None) -> list:
    out = []
    for m in (history or []):
        role = m.get("role")
        content = m.get("content") or ""
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def stream_wiki_answer(text: str, history: list | None, decision: dict):
    """命中知识库:把检索资料 + 自定义提示词喂给大模型,流式生成回答。"""
    yield from stream_messages(build_answer_messages(text, history, decision))


def stream_llm_answer(text: str, history: list | None = None):
    """未命中知识库:不带资料,直接让大模型基于通用经验流式回答。"""
    yield from stream_messages(build_answer_messages(text, history, {"source": "llm"}))
