"""共享 Markdown 案例解析工具的单元测试。"""

from __future__ import annotations

import pytest

from llm_wiki.common.markdown_case import (
    annotate,
    normalize_json_text,
    read_doc,
    section,
    split_frontmatter,
)

DOC = (
    "---\n"
    "title: HikariPool 连接池耗尽\n"
    "status: verified\n"
    "signatures:\n"
    "- HikariPool-1 - Connection is not available\n"
    "---\n"
    "## 问题背景\n大促高峰连接池耗尽\n\n"
    "## 解决方案\n加索引并调大 maximumPoolSize\n"
)


def test_split_frontmatter_parses_yaml():
    fm, body = split_frontmatter(DOC)
    assert fm["title"] == "HikariPool 连接池耗尽"
    assert fm["signatures"] == ["HikariPool-1 - Connection is not available"]
    assert "## 解决方案" in body


def test_split_frontmatter_no_frontmatter():
    fm, body = split_frontmatter("# 没有 frontmatter\n正文")
    assert fm == {}
    assert body == "# 没有 frontmatter\n正文"


def test_split_frontmatter_malformed_yaml_is_tolerant():
    fm, _ = split_frontmatter("---\n: : : bad\n---\nbody")
    assert fm == {}


def test_read_doc_wraps_io_errors_with_path(tmp_path):
    missing = tmp_path / "missing.md"

    with pytest.raises(OSError, match="missing.md") as exc:
        read_doc(missing)

    assert isinstance(exc.value.__cause__, OSError)


def test_section_extracts_until_next_heading():
    _, body = split_frontmatter(DOC)
    assert section(body, "问题背景") == "大促高峰连接池耗尽"
    assert section(body, "解决方案") == "加索引并调大 maximumPoolSize"
    assert section(body, "不存在") == ""


def test_annotate_combines_status_and_confidence():
    assert "draft" in annotate("draft", "high")
    assert annotate("verified", "high") == "✓ 已复核(verified)"
    assert "置信度 low" in annotate("verified", "low")


def test_normalize_json_text_strips_fence_and_prose():
    assert normalize_json_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert normalize_json_text('前言 {"a": 1} 后记') == '{"a": 1}'
    assert normalize_json_text('```\n{"x": 2}\n```') == '{"x": 2}'


def test_normalize_json_text_replaces_nonstandard_structural_whitespace_only():
    text = '{\u00a0"title": "保留\u00a0字段值",\u00a0"tags": ["npu"]\u00a0}'

    assert normalize_json_text(text) == '{ "title": "保留\u00a0字段值", "tags": ["npu"] }'
