"""Aho-Corasick 自动机与精确命中匹配器的单元测试。"""

from __future__ import annotations

from llm_wiki.search_index.aho_corasick import Automaton
from llm_wiki.search_index.common import ExactMatcher, order_exact_case_ids


def test_automaton_classic_overlapping_patterns():
    ac = Automaton()
    for w in ("he", "she", "his", "hers"):
        ac.add(w)
    ac.build()
    # "ushers" 子串含 she / he / hers,但不含 his
    assert ac.iter_matches("ushers") == {"she", "he", "hers"}


def test_automaton_no_match_returns_empty():
    ac = Automaton()
    ac.add("timeout")
    ac.build()
    assert ac.iter_matches("everything is fine") == set()


def test_automaton_empty_is_safe():
    ac = Automaton()
    ac.build()
    assert ac.iter_matches("anything") == set()


def test_exact_matcher_is_case_insensitive_substring():
    sig = "HikariPool-1 - Connection is not available, request timed out"
    m = ExactMatcher.from_rows([("hikari", sig)])
    log = f"2026-06-13 ERROR {sig.upper()} after 30007ms"
    matched = m.match(log.lower())
    assert matched == {"hikari": [sig]}


def test_exact_matcher_maps_shared_signature_to_all_cases():
    m = ExactMatcher.from_rows([("case-a", "Read timed out"), ("case-b", "read TIMED out")])
    matched = m.match("caused by: read timed out")
    assert set(matched) == {"case-a", "case-b"}


def test_order_exact_case_ids_prefers_more_and_longer_signatures():
    matched = {
        "few": ["abcd"],
        "many": ["abcd", "efghij"],
    }
    ordered = order_exact_case_ids(matched, limit=3)
    assert [cid for cid, _ in ordered] == ["many", "few"]


def test_order_exact_case_ids_respects_limit():
    matched = {f"c{i}": ["sig" + str(i)] for i in range(10)}
    assert len(order_exact_case_ids(matched, limit=3)) == 3
