#!/usr/bin/env python3
"""SQLite + FTS5 检索索引后端。"""
from __future__ import annotations

import pathlib
import re
import sqlite3

from .common import (
    CASES_DIR,
    DB_PATH,
    SCHEMA_PATH,
    SearchBackend,
    annotate,
    case_from_file,
    done,
)


class SqliteSearch(SearchBackend):
    def __init__(self, db_path: pathlib.Path = DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self._ok = None

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        return conn

    def label(self) -> str:
        return str(self.db_path)

    def available(self) -> bool:
        """探测当前 sqlite3 是否支持 FTS5 + trigram。"""
        if self._ok is None:
            try:
                c = sqlite3.connect(":memory:")
                c.execute("CREATE VIRTUAL TABLE t_fts_probe USING fts5(probe_text, tokenize='trigram')")
                c.close()
                self._ok = True
            except Exception:
                self._ok = False
        return self._ok

    def _upsert(self, conn: sqlite3.Connection, case: dict) -> None:
        cid = case["id"]
        row = conn.execute("SELECT rowid FROM t_cases WHERE id=?", (cid,)).fetchone()
        if row:
            rid = row[0]
            conn.execute("DELETE FROM t_cases_fts WHERE rowid=?", (rid,))
            conn.execute("DELETE FROM t_cases WHERE rowid=?", (rid,))
            conn.execute("DELETE FROM t_case_signatures WHERE case_id=?", (cid,))
        sigs = case.get("signatures") or []
        comps = case.get("components") or []
        cur = conn.execute(
            """INSERT INTO t_cases
               (id, file, title, category, status, confidence, components,
                signatures_text, background, diagnosis, solution, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, case.get("file", ""), case.get("title", ""), case.get("category", ""),
             case.get("status", ""), case.get("confidence", ""), "\n".join(comps),
             "\n".join(sigs), case.get("background", ""), case.get("diagnosis", ""),
             case.get("solution", ""), case.get("updated_at", "")),
        )
        rid = cur.lastrowid
        body = "\n".join(filter(None, [case.get("background", ""),
                                       case.get("diagnosis", ""),
                                       case.get("solution", "")]))
        conn.execute(
            "INSERT INTO t_cases_fts(rowid, title, signatures_text, components, body) VALUES(?,?,?,?,?)",
            (rid, case.get("title", ""), "\n".join(sigs), "\n".join(comps), body),
        )
        for s in sigs:
            conn.execute("INSERT INTO t_case_signatures(case_id, signature) VALUES(?,?)", (cid, s))

    def index_case(self, case: dict) -> None:
        if not self.available() or not case or not case.get("id"):
            return
        conn = self._connect()
        try:
            self._upsert(conn, case)
            conn.commit()
        finally:
            conn.close()

    def remove_case(self, case_id: str) -> None:
        if not self.available():
            return
        conn = self._connect()
        try:
            row = conn.execute("SELECT rowid FROM t_cases WHERE id=?", (case_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM t_cases_fts WHERE rowid=?", (row[0],))
                conn.execute("DELETE FROM t_cases WHERE rowid=?", (row[0],))
                conn.execute("DELETE FROM t_case_signatures WHERE case_id=?", (case_id,))
                conn.commit()
        finally:
            conn.close()

    def reindex_all(self) -> int:
        """从 wiki/cases/ 整库重建。"""
        if not self.available():
            return 0
        conn = self._connect()
        try:
            conn.execute("DELETE FROM t_cases_fts")
            conn.execute("DELETE FROM t_cases")
            conn.execute("DELETE FROM t_case_signatures")
            n = 0
            for path in sorted(CASES_DIR.rglob("*.md")):
                case = case_from_file(path)
                if case:
                    self._upsert(conn, case)
                    n += 1
            conn.commit()
            return n
        finally:
            conn.close()

    def _count(self, conn: sqlite3.Connection) -> int:
        return conn.execute("SELECT count(*) FROM t_cases").fetchone()[0]

    def ensure_built(self) -> None:
        if not self.available():
            return
        conn = self._connect()
        try:
            empty = self._count(conn) == 0
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
            sig_rows = conn.execute("SELECT case_id, signature FROM t_case_signatures").fetchall()
            matched: dict[str, list] = {}
            for cid, sig in sig_rows:
                if sig and sig.lower() in log_low:
                    matched.setdefault(cid, []).append(sig)
            if matched:
                hits = []
                for cid, sigs in matched.items():
                    r = conn.execute(
                        "SELECT title, file, status, confidence, solution FROM t_cases WHERE id=?",
                        (cid,)).fetchone()
                    if not r:
                        continue
                    title, file, status, confidence, solution = r
                    hits.append({
                        "title": title, "file": file, "matched": sigs,
                        "status": status, "confidence": confidence,
                        "note": annotate(status, confidence),
                        "solution": solution or "(该案例无「解决方案」段落)",
                    })
                return done(started, {"mode": "exact", "hits": hits})

            match_q = fts_query(log)
            if match_q:
                try:
                    rows = conn.execute(
                        """SELECT c.title, c.file, c.status, bm25(t_cases_fts) AS score
                           FROM t_cases_fts JOIN t_cases c ON c.rowid = t_cases_fts.rowid
                           WHERE t_cases_fts MATCH ?
                           ORDER BY score ASC LIMIT ?""",
                        (match_q, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    hits = [{
                        "title": t, "file": f, "status": s,
                        "score": round(-score, 3),
                    } for (t, f, s, score) in rows]
                    return done(started, {"mode": "fuzzy", "hits": hits})

            return done(started, {"mode": "none", "hits": []})
        finally:
            conn.close()

    def stats(self) -> dict:
        if not self.available():
            return {"backend": "sqlite", "available": False, "db": str(self.db_path)}
        self.ensure_built()
        conn = self._connect()
        try:
            return {
                "backend": "sqlite",
                "available": True,
                "db": str(self.db_path),
                "cases": conn.execute("SELECT count(*) FROM t_cases").fetchone()[0],
                "signatures": conn.execute("SELECT count(*) FROM t_case_signatures").fetchone()[0],
            }
        finally:
            conn.close()


def fts_query(log: str) -> str:
    """把任意日志文本转成安全的 FTS5 MATCH 查询。"""
    terms = []
    for tok in re.findall(r"[A-Za-z]{3,}|\d{3,}|[一-鿿]+", log):
        if "一" <= tok[0] <= "鿿":
            if len(tok) < 3:
                continue
            terms.extend(tok[i:i + 3] for i in range(len(tok) - 2))
        else:
            terms.append(tok)
    seen, out = set(), []
    for t in sorted(dict.fromkeys(terms), key=len, reverse=True):
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append('"' + t.replace('"', '""') + '"')
        if len(out) >= 60:
            break
    return " OR ".join(out)
