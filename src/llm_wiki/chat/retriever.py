#!/usr/bin/env python3
"""
retriever.py — 对话的检索接口与 wiki 知识库实现。

把「这一轮要不要带知识库资料、带哪些」从对话编排(agent.py)里拆出来,做成可注入接口:
  - Retriever        协议:retrieve(text) -> decision 决策字典。
  - WikiRetriever    默认实现:走 query.search() 召回案例,从数据库索引表读取正文并注入资料。
  - NullRetriever    纯对话:永远不检索,直接交给大模型作答(只想要对话功能时用它)。

decision 字典结构(agent.build_answer_messages_* 消费):
  {
    "source":  "wiki" | "llm",
    "mode":    "exact" | "fuzzy" | "none",
    "elapsed_ms": int,                 # 检索耗时
    "refs":    [{"file","title"}],     # source=wiki 时引用的案例(给前端展示)
    "context": [{"title","file","background","diagnosis","solution"}],  # 注入大模型的资料
  }
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

logger = logging.getLogger("log_wiki.retriever")

# 模糊命中判定为「关联度大」的最低分(bm25 取负后,越大越相关)。可用环境变量覆盖。
FUZZY_THRESHOLD = float(os.environ.get("CHAT_FUZZY_THRESHOLD", "1.0"))


def llm_decision(elapsed_ms: int = 0) -> dict:
    """不带知识库资料、纯大模型作答的决策。"""
    return {"source": "llm", "mode": "none", "elapsed_ms": elapsed_ms, "refs": [], "context": []}


class Retriever(Protocol):
    """对话检索接口:输入用户文本,返回本轮决策字典。"""

    def retrieve(self, text: str) -> dict:
        raise NotImplementedError


class NullRetriever:
    """纯对话:不做任何检索,永远交给大模型作答。"""

    def retrieve(self, text: str) -> dict:
        return llm_decision()


class WikiRetriever:
    """知识库检索:精确命中或关联度足够的模糊命中时注入数据库中的案例资料(RAG)。"""

    def retrieve(self, text: str) -> dict:
        # 惰性导入:只在真正检索时才拉起 query/search_index,纯对话(NullRetriever)不受牵连。
        from llm_wiki.knowledge import query

        res = query.search(text)
        mode = res.get("mode", "none")
        hits = res.get("hits", [])
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
            context = query.get_contexts([p["file"] for p in picked_files])
            if context:
                refs = [{"file": c["file"], "title": c["title"]} for c in context]
                return {
                    "source": "wiki",
                    "mode": mode,
                    "elapsed_ms": elapsed,
                    "refs": refs,
                    "context": context,
                }

        # 关联度小 / 无命中 → 交给大模型(不带知识库资料)
        return llm_decision(elapsed)
