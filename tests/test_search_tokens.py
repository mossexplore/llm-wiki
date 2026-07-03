from __future__ import annotations

from llm_wiki.search_index import common
from llm_wiki.search_index.common import is_cjk, iter_search_tokens


def test_search_tokens_cover_full_basic_cjk_block():
    high_cjk = "\u9fff"

    assert iter_search_tokens(f"错误{high_cjk}") == [f"错误{high_cjk}"]
    assert is_cjk(high_cjk)


def test_case_from_file_ignores_non_dict_frontmatter(tmp_path, monkeypatch):
    path = tmp_path / "case.md"
    path.write_text("---\n[]\n---\nbody", encoding="utf-8")
    monkeypatch.setattr(common, "split_frontmatter", lambda _text: (["not", "dict"], "body"))

    assert common.case_from_file(path) is None
