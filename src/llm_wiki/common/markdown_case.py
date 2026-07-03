"""Markdown 案例的统一解析工具。

全项目共享一份 frontmatter / 正文段落 / 可信度标注 / 模型 JSON 归一化逻辑,
避免各模块各写一套正则导致行为漂移(历史上 query.py 用手写正则解析 YAML,
其余模块用 yaml.safe_load,两套解析器容易不一致)。
"""

from __future__ import annotations

import pathlib
import re

import yaml

_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.I)
_FENCE_CLOSE = re.compile(r"```\s*$")


def split_frontmatter(text: str) -> tuple:
    """切分 ``---\\n<yaml>\\n---\\n<body>``。

    无 frontmatter 或解析失败时返回 ``({}, 原文)``,调用方可据空 dict 判断。
    """
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm_text, body = text.split("---", 2)
    except ValueError:
        return {}, text
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(fm, dict):
        return {}, body
    return fm, body


def read_doc(path: pathlib.Path) -> tuple:
    """读取文件并切分 frontmatter,返回 ``(fm_dict, body)``。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"读取 Markdown 案例失败: {path}") from exc
    return split_frontmatter(text)


def section(body: str, title: str) -> str:
    """抽取 ``## <title>`` 段落正文(到下一个 ``##`` 或文末);缺失返回空串。"""
    m = re.search(rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    return m.group(1).strip() if m else ""


def annotate(status: str, confidence: str) -> str:
    """按 status / confidence 生成可信度标注文案。"""
    notes = []
    if status == "draft":
        notes.append("⚠ 该案例尚未复核(draft),仅供参考")
    elif status == "verified":
        notes.append("✓ 已复核(verified)")
    if confidence in ("low", "medium"):
        notes.append(f"置信度 {confidence},建议结合实际验证")
    return " | ".join(notes)


def normalize_json_text(text: str) -> str:
    """剥离 ``` ```json ``` ``` 代码围栏并裁出最外层 ``{...}``,供解析模型输出。"""
    txt = (text or "").strip()
    txt = _FENCE_OPEN.sub("", txt).strip()
    txt = _FENCE_CLOSE.sub("", txt).strip()
    if not txt.startswith("{"):
        first = txt.find("{")
        last = txt.rfind("}")
        if first != -1 and last > first:
            start = first
            end = last + 1
            txt = txt[start:end].strip()
    return txt
