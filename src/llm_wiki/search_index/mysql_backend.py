#!/usr/bin/env python3
"""MySQL FULLTEXT 检索索引后端。"""
from __future__ import annotations

import re

from llm_wiki.common import storage_config
from llm_wiki.common.mysql_client import _sql_text, get_mysql_client
from .common import (
    CASES_DIR,
    MYSQL_SCHEMA_PATH,
    SearchBackend,
    annotate,
    case_from_file,
    done,
)


class MySQLSearch(SearchBackend):
    def __init__(self):
        self._ok = None

    def label(self) -> str:
        cfg = storage_config.mysql_config()
        return f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"

    def _init_schema(self, conn) -> None:
        statements = [
            stmt.strip()
            for stmt in MYSQL_SCHEMA_PATH.read_text(encoding="utf-8").split(";")
            if stmt.strip()
        ]
        for stmt in statements:
            conn.execute(_sql_text(stmt))

    def available(self) -> bool:
        if self._ok is None:
            with get_mysql_client().begin() as conn:
                self._init_schema(conn)
                self._ok = True
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
        conn.execute(_sql_text(
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
        ), params)
        for s in sigs:
            conn.execute(
                _sql_text("INSERT INTO t_case_signatures(case_id, signature) VALUES(:case_id,:signature)"),
                {"case_id": cid, "signature": s},
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
            conn.execute(_sql_text("DELETE FROM t_case_signatures WHERE case_id=:case_id"), {"case_id": case_id})
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
        if not self.available():
            return
        with get_mysql_client().begin() as conn:
            row = conn.execute(_sql_text("SELECT count(*) AS n FROM t_cases")).mappings().one()
            empty = row["n"] == 0
        if empty and any(case_from_file(p) for p in CASES_DIR.rglob("*.md")):
            self.reindex_all()

    def search(self, log: str, limit: int = 3) -> dict | None:
        if not self.available():
            return None
        import time
        started = time.perf_counter()
        self.ensure_built()
        log_low = log.lower()
        with get_mysql_client().begin() as conn:
            sig_rows = conn.execute(_sql_text("SELECT case_id, signature FROM t_case_signatures")).mappings().all()
            matched: dict[str, list] = {}
            for row in sig_rows:
                sig = row["signature"]
                if sig and sig.lower() in log_low:
                    matched.setdefault(row["case_id"], []).append(sig)
            if matched:
                hits = []
                for cid, sigs in matched.items():
                    r = conn.execute(
                        _sql_text("SELECT title, file, status, confidence, solution FROM t_cases WHERE id=:case_id"),
                        {"case_id": cid},
                    ).mappings().first()
                    if not r:
                        continue
                    hits.append({
                        "title": r["title"], "file": r["file"], "matched": sigs,
                        "status": r["status"], "confidence": r["confidence"],
                        "note": annotate(r["status"], r["confidence"]),
                        "solution": r["solution"] or "(该案例无「解决方案」段落)",
                    })
                return done(started, {"mode": "exact", "hits": hits})

            query_text = mysql_query(log)
            if query_text:
                rows = conn.execute(
                    _sql_text("""SELECT title, file, status,
                              MATCH(title, signatures_text, components, background, diagnosis, solution)
                              AGAINST(:query_text IN NATURAL LANGUAGE MODE) AS score
                       FROM t_cases
                       WHERE MATCH(title, signatures_text, components, background, diagnosis, solution)
                             AGAINST(:query_text IN NATURAL LANGUAGE MODE)
                       ORDER BY score DESC LIMIT :limit"""),
                    {"query_text": query_text, "limit": limit},
                ).mappings().all()
                if rows:
                    hits = [{
                        "title": r["title"], "file": r["file"], "status": r["status"],
                        "score": round(float(r["score"] or 0), 3),
                    } for r in rows]
                    return done(started, {"mode": "fuzzy", "hits": hits})
            return done(started, {"mode": "none", "hits": []})

    def stats(self) -> dict:
        if not self.available():
            return {"backend": "mysql", "available": False, "db": self.label()}
        self.ensure_built()
        with get_mysql_client().begin() as conn:
            cases = conn.execute(_sql_text("SELECT count(*) AS n FROM t_cases")).mappings().one()["n"]
            signatures = conn.execute(_sql_text("SELECT count(*) AS n FROM t_case_signatures")).mappings().one()["n"]
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
    for tok in re.findall(r"[A-Za-z]{3,}|\d{3,}|[一-鿿]+", log):
        if "一" <= tok[0] <= "鿿":
            terms.append(tok[:120])
        else:
            terms.append(tok)
        if len(terms) >= 80:
            break
    return " ".join(dict.fromkeys(terms))
