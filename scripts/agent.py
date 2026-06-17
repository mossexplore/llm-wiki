#!/usr/bin/env python3
"""
agent.py — 对话 Agent 的「先检索、后兜底」编排层

回答策略(对应需求 2/3):
  1) 先用 query.search() 检索本地知识库:
     - 精确命中(mode=exact):signature 原文匹配 → 直接用命中案例的解决方案回答,
       并在答案里标注来源 wiki。关联度最高。
     - 模糊命中且关联度足够大(mode=fuzzy 且 top score >= 阈值)→ 用最相关案例的
       解决方案回答,标注来源 wiki(并提示需人工判断)。
  2) 没有相关案例(mode=none),或模糊命中但关联度太小(score < 阈值)→ 明确说明
     知识库未找到,转而调用大模型(OpenAI 兼容接口)流式回答。

所有「回答文本」都以增量(delta)yield 出来,满足前端流式展示的要求:
  - wiki 答案:本地已有完整文本,按小片切块「伪流式」吐出,体验与大模型一致;
  - 大模型答案:真正的 OpenAI 流式 token。

阈值 CHAT_FUZZY_THRESHOLD 可用环境变量调,默认 1.0(bm25 取负后越大越相关)。
"""
from __future__ import annotations
import os, re, sys, time, pathlib, logging

try:
    import query
    import ingest
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import query
    import ingest

ROOT = pathlib.Path(__file__).resolve().parent.parent
logger = logging.getLogger("log_wiki.agent")

# 模糊命中判定为「关联度大」的最低分(bm25 取负后,越大越相关)。可用环境变量覆盖。
FUZZY_THRESHOLD = float(os.environ.get("CHAT_FUZZY_THRESHOLD", "1.0"))

CHAT_SYSTEM_PROMPT = (
    "你是 llm-wiki 的运维排查助手。本地知识库里没有检索到相关案例,所以下面由你来回答。"
    "请基于通用的工程与运维经验,给出有条理、可操作的排查建议;"
    "不确定时要诚实说明,不要编造不存在的日志、错误码或结论。请用中文回答。"
)


def _solution_of_file(file_rel: str) -> tuple[str, str]:
    """读 wiki/cases/<file> 取(标题, 解决方案);用于给模糊命中补全解决方案文本。"""
    path = (ROOT / file_rel).resolve()
    try:
        path.relative_to((ROOT / "wiki" / "cases").resolve())
    except ValueError:
        return "", ""
    if not path.exists():
        return "", ""
    text = path.read_text(encoding="utf-8")
    title = path.stem
    m = re.search(r"^title:[ \t]*(.+)$", text, re.M)
    if m:
        title = m.group(1).strip().strip('"\'')
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    return title, query.solution_of(body)


def retrieve(text: str) -> dict:
    """决定本轮怎么答:返回决策字典。

    {
      "source": "wiki" | "llm",
      "mode":   "exact" | "fuzzy" | "none",
      "elapsed_ms": int,                # 检索耗时
      "refs":   [{"file","title"}],     # source=wiki 时的来源案例
      "answer": str,                    # source=wiki 时的完整答案文本(待流式吐出)
    }
    """
    res = query.search(text)
    mode = res.get("mode", "none")
    hits = res.get("hits", []) or []
    elapsed = res.get("elapsed_ms", 0)

    if mode == "exact" and hits:
        refs = [{"file": h["file"], "title": h.get("title", "")} for h in hits]
        answer = _format_wiki_answer(hits, related=False)
        return {"source": "wiki", "mode": "exact", "elapsed_ms": elapsed, "refs": refs, "answer": answer}

    if mode == "fuzzy" and hits:
        top = hits[0]
        score = top.get("score")
        if isinstance(score, (int, float)) and score >= FUZZY_THRESHOLD:
            # 关联度大:取最相关的(最多 2 条)补全解决方案后用 wiki 答案
            picked = []
            for h in hits[:2]:
                if isinstance(h.get("score"), (int, float)) and h["score"] < FUZZY_THRESHOLD:
                    continue
                title, solution = _solution_of_file(h["file"])
                picked.append({
                    "title": title or h.get("title", ""),
                    "file": h["file"], "solution": solution, "score": h.get("score"),
                })
            if picked:
                refs = [{"file": p["file"], "title": p["title"]} for p in picked]
                answer = _format_wiki_answer(picked, related=True)
                return {"source": "wiki", "mode": "fuzzy", "elapsed_ms": elapsed, "refs": refs, "answer": answer}

    # 关联度小 / 无命中 → 交给大模型
    return {"source": "llm", "mode": mode, "elapsed_ms": elapsed, "refs": [], "answer": ""}


def _format_wiki_answer(hits: list, related: bool) -> str:
    """把命中案例拼成一段带来源标注的 Markdown 答案。"""
    if related:
        head = "我在知识库中找到**关联度较高**的案例,供你参考(建议结合实际情况判断):\n\n"
    else:
        head = "我在知识库中**精确命中**了相关案例:\n\n"
    blocks = []
    for h in hits:
        title = h.get("title") or h.get("file", "")
        solution = (h.get("solution") or "").strip() or "(该案例暂无「解决方案」段落)"
        file = h.get("file", "")
        blocks.append(f"### {title}\n\n{solution}\n\n> 来源 wiki:`{file}`")
    return head + "\n\n".join(blocks)


def _chunks(text: str, size: int = 24):
    """把完整文本切成小片,模拟流式吐出(让 wiki 答案也有逐字出现的体验)。"""
    for i in range(0, len(text), size):
        yield text[i:i + size]


def stream_wiki_answer(decision: dict):
    """流式吐出 wiki 答案(伪流式:本地文本按小片 yield)。"""
    for piece in _chunks(decision.get("answer", "")):
        yield piece


def stream_llm_answer(text: str, history: list | None = None):
    """调用大模型流式回答。history 为既往消息([{role,content}...]),用于多轮上下文。"""
    client, model = ingest._client_and_model()
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    for m in (history or []):
        role = m.get("role")
        content = m.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})
    stream = client.chat.completions.create(
        model=model,
        temperature=0.3,
        messages=messages,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
