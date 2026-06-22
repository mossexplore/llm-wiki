#!/usr/bin/env python3
"""MySQL FULLTEXT 检索索引后端。"""
from __future__ import annotations

import re

from shared import storage_config
from search_indexing.common import (
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

    def _pymysql(self):
        try:
            import pymysql
            import pymysql.cursors
            return pymysql
        except ImportError as exc:
            raise RuntimeError("使用 MySQL 存储需安装 PyMySQL: pip install PyMySQL") from exc

    def _connect(self):
        pymysql = self._pymysql()
        return pymysql.connect(
            **storage_config.mysql_connection_kwargs(),
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _init_schema(self, conn) -> None:
        statements = [
            stmt.strip()
            for stmt in MYSQL_SCHEMA_PATH.read_text(encoding="utf-8").split(";")
            if stmt.strip()
        ]
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()

    def available(self) -> bool:
        if self._ok is None:
            conn = self._connect()
            try:
                self._init_schema(conn)
                self._ok = True
            finally:
                conn.close()
        return self._ok

    def _upsert(self, cur, case: dict) -> None:
        cid = case["id"]
        sigs = case.get("signatures") or []
        comps = case.get("components") or []
        cur.execute("DELETE FROM case_signatures WHERE case_id=%s", (cid,))
        cur.execute(
            """INSERT INTO cases
               (id, file, title, category, status, confidence, components,
                signatures_text, background, diagnosis, solution, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 file=VALUES(file), title=VALUES(title), category=VALUES(category),
                 status=VALUES(status), confidence=VALUES(confidence),
                 components=VALUES(components), signatures_text=VALUES(signatures_text),
                 background=VALUES(background), diagnosis=VALUES(diagnosis),
                 solution=VALUES(solution), updated_at=VALUES(updated_at)""",
            (cid, case.get("file", ""), case.get("title", ""), case.get("category", ""),
             case.get("status", ""), case.get("confidence", ""), "\n".join(comps),
             "\n".join(sigs), case.get("background", ""), case.get("diagnosis", ""),
             case.get("solution", ""), case.get("updated_at", "")),
        )
        for s in sigs:
            cur.execute("INSERT INTO case_signatures(case_id, signature) VALUES(%s,%s)", (cid, s))

    def index_case(self, case: dict) -> None:
        if not self.available() or not case or not case.get("id"):
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                self._upsert(cur, case)
            conn.commit()
        finally:
            conn.close()

    def remove_case(self, case_id: str) -> None:
        if not self.available():
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM case_signatures WHERE case_id=%s", (case_id,))
                cur.execute("DELETE FROM cases WHERE id=%s", (case_id,))
            conn.commit()
        finally:
            conn.close()

    def reindex_all(self) -> int:
        if not self.available():
            return 0
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM case_signatures")
                cur.execute("DELETE FROM cases")
                n = 0
                for path in sorted(CASES_DIR.rglob("*.md")):
                    case = case_from_file(path)
                    if case:
                        self._upsert(cur, case)
                        n += 1
            conn.commit()
            return n
        finally:
            conn.close()

    def ensure_built(self) -> None:
        if not self.available():
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM cases")
                empty = cur.fetchone()["n"] == 0
        finally:
            conn.close()
        if empty and any(case_from_file(p) for p in CASES_DIR.rglob("*.md")):
            self.reindex_all()

    def search(self, log: str, limit: int = 3) -> dict | None:
        if not self.available():
            return None
        import time
        started = time.perf_counter()
        self.ensure_built()
        log_low = log.lower()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT case_id, signature FROM case_signatures")
                matched: dict[str, list] = {}
                for row in cur.fetchall():
                    sig = row["signature"]
                    if sig and sig.lower() in log_low:
                        matched.setdefault(row["case_id"], []).append(sig)
                if matched:
                    hits = []
                    for cid, sigs in matched.items():
                        cur.execute(
                            "SELECT title, file, status, confidence, solution FROM cases WHERE id=%s",
                            (cid,),
                        )
                        r = cur.fetchone()
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
                    cur.execute(
                        """SELECT title, file, status,
                                  MATCH(title, signatures_text, components, background, diagnosis, solution)
                                  AGAINST(%s IN NATURAL LANGUAGE MODE) AS score
                           FROM cases
                           WHERE MATCH(title, signatures_text, components, background, diagnosis, solution)
                                 AGAINST(%s IN NATURAL LANGUAGE MODE)
                           ORDER BY score DESC LIMIT %s""",
                        (query_text, query_text, limit),
                    )
                    rows = cur.fetchall()
                    if rows:
                        hits = [{
                            "title": r["title"], "file": r["file"], "status": r["status"],
                            "score": round(float(r["score"] or 0), 3),
                        } for r in rows]
                        return done(started, {"mode": "fuzzy", "hits": hits})
                return done(started, {"mode": "none", "hits": []})
        finally:
            conn.close()

    def stats(self) -> dict:
        if not self.available():
            return {"backend": "mysql", "available": False, "db": self.label()}
        self.ensure_built()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM cases")
                cases = cur.fetchone()["n"]
                cur.execute("SELECT count(*) AS n FROM case_signatures")
                signatures = cur.fetchone()["n"]
            return {
                "backend": "mysql",
                "available": True,
                "db": self.label(),
                "cases": cases,
                "signatures": signatures,
            }
        finally:
            conn.close()


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
