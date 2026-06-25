"""检索质量评测的回归门槛。

数据集与 evaluate 逻辑在 llm_wiki.search_index.eval(包内,Web/CLI 共用)。
本文件只把它当回归门槛:指标跌破地板线即失败。

  pytest tests/test_retrieval_eval.py                       # 回归门槛
  python -m llm_wiki.search_index.eval                      # 打印分项基线表
  EVAL_BACKEND=mysql python -m llm_wiki.search_index.eval   # 评 MySQL FULLTEXT 后端
"""

from __future__ import annotations

import pytest

from llm_wiki.search_index.eval import CORPUS, K, build_sandbox_backend, evaluate, run_eval


@pytest.fixture()
def sqlite_backend(tmp_path):
    b = build_sandbox_backend(tmp_path / "eval.db")
    if b is None:
        pytest.skip("当前 sqlite3 不支持 FTS5 + trigram,跳过检索评测")
    return b


def test_exact_queries_all_hit(sqlite_backend):
    """原文粘贴 signature 必须 100% 精确命中 —— 这是检索的硬底线。"""
    report = evaluate(sqlite_backend)
    assert report["by_kind"]["exact"][f"recall@{K}"] == 1.0, report["by_kind"]


def test_overall_baseline_floor(sqlite_backend):
    """锁定当前整体基线,跌破即说明改动造成回归(地板线保守,留出抖动余量)。"""
    overall = evaluate(sqlite_backend)["overall"]
    assert overall[f"recall@{K}"] >= 0.70, overall
    assert overall["mrr"] >= 0.60, overall


def test_run_eval_sandbox_report_shape():
    """run_eval 在隔离沙箱里产出完整报告,供 Web/API 直接消费。"""
    report = run_eval(K)
    if not report.get("ok"):
        pytest.skip(report.get("reason", "评测沙箱不可用"))
    assert report["corpus_size"] == len(CORPUS)
    assert report["query_count"] == len(report["rows"])
    assert set(report["by_kind"]) == {"exact", "lexical", "semantic"}
    assert report["modes"]
