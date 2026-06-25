#!/usr/bin/env python3
"""MySQL FULLTEXT 检索索引后端。"""

from __future__ import annotations

import uuid

from llm_wiki.common.mysql_client import (
    _sql_text,
    get_mysql_client,
    get_mysql_label,
    run_mysql_schema,
)

from .common import (
    CASES_DIR,
    MYSQL_SCHEMA_PATH,
    SearchBackend,
    annotate,
    case_from_file,
    done,
    exact_signatures,
    is_cjk,
    iter_search_tokens,
    logger,
)


class MySQLSearch(SearchBackend):
    def __init__(self):
        self._ok = None
        self._built = False

    def label(self) -> str:
        return get_mysql_label()

    def _init_schema(self, conn) -> None:
        run_mysql_schema(conn, MYSQL_SCHEMA_PATH)

    def available(self) -> bool:
        """探测 MySQL 是否可用并确保建表。

        失败时返回 False(而非抛异常),让 query.search() 能回退到文件扫描;
        且不缓存 False —— 数据库恢复后下次调用会重试,避免一次抖动永久禁用检索。
        """
        if self._ok:
            return True
        try:
            with get_mysql_client().begin() as conn:
                self._init_schema(conn)
            self._ok = True
        except Exception:
            logger.exception("search_index mysql backend unavailable, fallback to file scan")
            self._ok = False
        return self._ok

    def _upsert(self, conn, case: dict) -> None:
        cid = case["id"]
        sigs = case.get("signatures") or []
        comps = case.get("components") or []
        conn.execute(_sql_text("DELETE FROM t_case_signatures WHERE case_id=:case_id"), {"case_id": cid})
        params = {
            "id": cid,
            "file": case.get("file", ""),
            "title": case.get("title", ""),
            "category": case.get("category", ""),
            "status": case.get("status", ""),
            "confidence": case.get("confidence", ""),
            "components": "\n".join(comps),
            "signatures_text": "\n".join(sigs),
            "background": case.get("background", ""),
            "diagnosis": case.get("diagnosis", ""),
            "solution": case.get("solution", ""),
            "updated_at": case.get("updated_at", ""),
        }
        conn.execute(
            _sql_text(
                """INSERT INTO t_cases
               (id, file, title, category, status, confidence, components,
                signatures_text, background, diagnosis, solution, updated_at)
               VALUES (:id,:file,:title,:category,:status,:confidence,:components,
                       :signatures_text,:background,:diagnosis,:solution,:updated_at)
               ON DUPLICATE KEY UPDATE
                 file=VALUES(file), title=VALUES(title), category=VALUES(category),
                 status=VALUES(status), confidence=VALUES(confidence),
                 components=VALUES(components), signatures_text=VALUES(signatures_text),
                 background=VALUES(background), diagnosis=VALUES(diagnosis),
                 solution=VALUES(solution), updated_at=VALUES(updated_at)"""
            ),
            params,
        )
        for s in exact_signatures(sigs):
            conn.execute(
                # INSERT IGNORE: UNIQUE(case_id, signature(255)) 兜底去重,
                # 避免极端情况下(同案例两条 signature 共享前 255 字符)整次索引报错中断。
                _sql_text(
                    "INSERT IGNORE INTO t_case_signatures(id, case_id, signature) "
                    "VALUES(:id,:case_id,:signature)"
                ),
                {"id": str(uuid.uuid4()), "case_id": cid, "signature": s},
            )

    def index_case(self, case: dict) -> None:
        if not self.available() or not case or not case.get("id"):
            return
        with get_mysql_client().begin() as conn:
            self._upsert(conn, case)

    def remove_case(self, case_id: str) -> None:
        if not self.available():
            return
        with get_mysql_client().begin() as conn:
            conn.execute(
                _sql_text("DELETE FROM t_case_signatures WHERE case_id=:case_id"), {"case_id": case_id}
            )
            conn.execute(_sql_text("DELETE FROM t_cases WHERE id=:case_id"), {"case_id": case_id})

    def reindex_all(self) -> int:
        if not self.available():
            return 0
        with get_mysql_client().begin() as conn:
            conn.execute(_sql_text("DELETE FROM t_case_signatures"))
            conn.execute(_sql_text("DELETE FROM t_cases"))
            n = 0
            for path in sorted(CASES_DIR.rglob("*.md")):
                case = case_from_file(path)
                if case:
                    self._upsert(conn, case)
                    n += 1
            return n

    def ensure_built(self) -> None:
        # 进程内只做一次空库自检:首次确认非空(或自动重建)后置位 _built,
        # 避免每次 search() 都多打一次 SELECT count(*) 往返(热路径)。
        if self._built:
            return
        if not self.available():
            return
        with get_mysql_client().begin() as conn:
            row = conn.execute(_sql_text("SELECT count(*) AS n FROM t_cases")).mappings().one()
            empty = row["n"] == 0
        if empty and any(case_from_file(p) for p in CASES_DIR.rglob("*.md")):
            self.reindex_all()
        self._built = True

    def search(self, log: str, limit: int = 3) -> dict | None:
        if not self.available():
            return None
        import time

        started = time.perf_counter()
        try:
            return self._search(conn_started=started, log=log, limit=limit)
        except Exception:
            # available() 只兜底连接/建表;此处兜底运行期查询异常(collation 冲突、
            # 旧表残留导致 FULLTEXT 列集不匹配等),返回 None 让 query.search() 回退文件扫描,
            # 与 SQLite 后端对 MATCH 的容错行为对齐。
            logger.exception("search_index mysql query failed, fallback to file scan")
            return None

    def _search(self, conn_started, log: str, limit: int) -> dict:
        started = conn_started
        self.ensure_built()
        log_low = log.lower()
        with get_mysql_client().begin() as conn:
            # 子串判断下推到 MySQL(LOCATE),避免每次查询把整张 signature 表拉回应用层。
            sig_rows = (
                conn.execute(
                    _sql_text(
                        "SELECT case_id, signature FROM t_case_signatures "
                        "WHERE LOCATE(LOWER(signature), :log) > 0"
                    ),
                    {"log": log_low},
                )
                .mappings()
                .all()
            )
            matched: dict[str, list] = {}
            for row in sig_rows:
                matched.setdefault(row["case_id"], []).append(row["signature"])
            if matched:
                # 精确命中按相关性排序并截断到 limit:命中 signature 数 > 最长 signature 长度
                # > case_id(确定性兜底)。否则一条通用 signature 命中多案例时会无序、无界返回,
                # 破坏 search(log, limit) 契约。
                ordered = sorted(
                    matched.items(),
                    key=lambda kv: (len(kv[1]), max(len(s) for s in kv[1]), kv[0]),
                    reverse=True,
                )[:limit]
                hits = []
                for cid, sigs in ordered:
                    r = (
                        conn.execute(
                            _sql_text(
                                "SELECT title, file, status, confidence, solution "
                                "FROM t_cases WHERE id=:case_id"
                            ),
                            {"case_id": cid},
                        )
                        .mappings()
                        .first()
                    )
                    if not r:
                        continue
                    hits.append(
                        {
                            "title": r["title"],
                            "file": r["file"],
                            "matched": sigs,
                            "status": r["status"],
                            "confidence": r["confidence"],
                            "note": annotate(r["status"], r["confidence"]),
                            "solution": r["solution"] or "(该案例无「解决方案」段落)",
                        }
                    )
                return done(started, {"mode": "exact", "source": "mysql", "hits": hits})

            query_text = mysql_query(log)
            if query_text:
                rows = (
                    conn.execute(
                        _sql_text("""SELECT title, file, status,
                              MATCH(title, signatures_text, components, background, diagnosis, solution)
                              AGAINST(:query_text IN NATURAL LANGUAGE MODE) AS score
                       FROM t_cases
                       WHERE MATCH(title, signatures_text, components, background, diagnosis, solution)
                             AGAINST(:query_text IN NATURAL LANGUAGE MODE)
                       ORDER BY score DESC LIMIT :limit"""),
                        {"query_text": query_text, "limit": limit},
                    )
                    .mappings()
                    .all()
                )
                if rows:
                    hits = [
                        {
                            "title": r["title"],
                            "file": r["file"],
                            "status": r["status"],
                            "score": round(float(r["score"] or 0), 3),
                        }
                        for r in rows
                    ]
                    return done(started, {"mode": "fuzzy", "source": "mysql", "hits": hits})
            return done(started, {"mode": "none", "source": "mysql", "hits": []})

    def stats(self) -> dict:
        if not self.available():
            return {"backend": "mysql", "available": False, "db": self.label()}
        self.ensure_built()
        with get_mysql_client().begin() as conn:
            cases = conn.execute(_sql_text("SELECT count(*) AS n FROM t_cases")).mappings().one()["n"]
            signatures = (
                conn.execute(_sql_text("SELECT count(*) AS n FROM t_case_signatures")).mappings().one()["n"]
            )
        return {
            "backend": "mysql",
            "available": True,
            "db": self.label(),
            "cases": cases,
            "signatures": signatures,
        }


def mysql_query(log: str) -> str:
    """把日志文本压缩成 MySQL FULLTEXT 自然语言查询文本。"""
    terms = []
    for tok in iter_search_tokens(log):
        terms.append(tok[:120] if is_cjk(tok) else tok)
        if len(terms) >= 80:
            break
    return " ".join(dict.fromkeys(terms))
