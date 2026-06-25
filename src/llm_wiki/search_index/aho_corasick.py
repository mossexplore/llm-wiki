#!/usr/bin/env python3
"""纯 Python Aho-Corasick 多模式子串匹配。

把一批模式串编译成一个带失配指针的状态机,之后对任意文本扫一遍(O(文本长度 + 命中数))
即可找出其中出现的全部模式串 —— 与「逐条模式串去匹配文本」相比,耗时基本与模式串数量无关。

检索里用它替换精确命中的 `LOCATE(signature, log)` 全表扫描:把全部 signature 建成自动机,
用户日志走一遍就拿到命中的 signature 集合。无外部依赖,规模到上万模式串仍是内存级毫秒操作。
"""

from __future__ import annotations

from collections import deque


class Automaton:
    """多模式串自动机:add() 加模式 → build() 定型 → iter_matches() 扫文本取命中模式集合。"""

    def __init__(self) -> None:
        self._goto: list[dict] = [{}]   # 节点 -> {字符: 子节点};节点 0 是 root
        self._fail: list[int] = [0]     # 节点 -> 失配指针
        self._out: list[list] = [[]]    # 节点 -> 在此结束的模式串列表
        self._built = False

    def add(self, pattern: str) -> None:
        """加入一个模式串(调用方负责大小写归一)。"""
        if not pattern:
            return
        node = 0
        for ch in pattern:
            nxt = self._goto[node].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto.append({})
                self._fail.append(0)
                self._out.append([])
                self._goto[node][ch] = nxt
            node = nxt
        self._out[node].append(pattern)
        self._built = False

    def build(self) -> None:
        """BFS 计算失配指针并合并输出链;add() 之后、匹配之前调用(iter_matches 会自动兜底)。"""
        q: deque[int] = deque()
        for child in self._goto[0].values():
            self._fail[child] = 0
            q.append(child)
        while q:
            node = q.popleft()
            for ch, nxt in self._goto[node].items():
                q.append(nxt)
                f = self._fail[node]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                target = self._goto[f].get(ch, 0)
                self._fail[nxt] = 0 if target == nxt else target
                # 把失配节点的输出并进来:走到 nxt 时,其所有后缀模式也一并命中
                if self._out[self._fail[nxt]]:
                    self._out[nxt] = self._out[nxt] + self._out[self._fail[nxt]]
        self._built = True

    def iter_matches(self, text: str) -> set:
        """扫一遍 text,返回其中作为子串出现的模式串集合。"""
        if not self._built:
            self.build()
        goto, fail, out = self._goto, self._fail, self._out
        found: set = set()
        node = 0
        for ch in text:
            while node and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            if out[node]:
                found.update(out[node])
        return found
