#!/usr/bin/env python3
"""
search_index.py — 检索索引后端（阶段 1：SQLite + FTS5 trigram）

定位与护栏
  - wiki/cases/*.md 永远是知识的权威源(OKF 不可变层);本模块维护的 SQLite 库只是
    「派生索引」,用于把「模糊召回」从全量读文件 + token 交集,升级成 BM25 全文检索。
  - 入库/更新/删除知识时由后端(server.py)同步本索引;索引可随时从文件整库重建。
  - signatures「精确命中」仍在应用层做子串匹配(见 search()),它是检索命门 + 无命中门控
    的依据,优先级最高,不交给全文检索的相关度排序。

为什么是「接口 + 实现」
  现在用本地 SQLite(零网络、零费用,契合现有护栏),语法与 Cloudflare D1 一致;
  后期要换 D1 / MySQL 时,只需新增一个实现类,query.py / server.py 调用面不变。
  迁移要点见 db/README.md 与 db/schema.mysql.sql。

可单独运行(便于排障):
    python scripts/search_index.py reindex        # 从 wiki/cases/ 整库重建索引
    python scripts/search_index.py search "报错文本"
    python scripts/search_index.py stats
"""
from __future__ import annotations   # 延迟注解求值,兼容 Python 3.9（X | None 等）
import os, re, sys, json, sqlite3, pathlib, datetime, logging, yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "wiki" / "cases"
DB_PATH = pathlib.Path(os.environ.get("SEARCH_DB", ROOT / "index" / "search.db"))
SCHEMA_PATH = ROOT / "db" / "schema.sqlite.sql"
logger = logging.getLogger("log_wiki.search_index")


# ----------------------------- 抽象接口 -----------------------------
class SearchBackend:
    """检索后端接口。今天的实现是 SQLite;换 D1 / MySQL 时实现同一组方法即可。

    规范化的「案例字典」(index_case 的入参)字段:
      id, file, title, category, status, confidence,
      signatures(list), components(list), background, diagnosis, solution, updated_at
    """

    def available(self) -> bool: raise NotImplementedError
    def reindex_all(self) -> int: raise NotImplementedError
    def index_case(self, case: dict) -> None: raise NotImplementedError
    def remove_case(self, case_id: str) -> None: raise NotImplementedError
    def search(self, log: str, limit: int = 3) -> dict | None: raise NotImplementedError


# --------------------- Markdown -> 规范化案例字典 ---------------------
def _section(body: str, title: str) -> str:
    m = re.search(rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    return m.group(1).strip() if m else ""


def case_from_file(path: pathlib.Path) -> dict | None:
    """解析单个 wiki/cases/*.md 为规范化案例字典;非案例文件返回 None。"""
    if path.name in ("index.md", "log.md"):
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    try:
        _, fm_text, body = text.split("---", 2)
    except ValueError:
        return None
    fm = yaml.safe_load(fm_text) or {}
    sigs = fm.get("signatures") or []
    if isinstance(sigs, str):
        sigs = [sigs]
    comps = fm.get("components") or []
    if isinstance(comps, str):
        comps = [comps]
    return {
        "id": path.stem,                                  # slug,与文件读写用的 key 一致
        "file": str(path.relative_to(ROOT)),
        "title": fm.get("title") or path.stem,
        "category": fm.get("category") or "未分类",
        "status": fm.get("status") or "verified",
        "confidence": fm.get("confidence") or "unknown",
        "signatures": [str(s) for s in sigs if str(s).strip()],
        "components": [str(c) for c in comps if str(c).strip()],
        "background": _section(body, "问题背景"),
        "diagnosis": _section(body, "定位过程"),
        "solution": _section(body, "解决方案"),
        "updated_at": datetime.datetime.fromtimestamp(
            path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def _annotate(status: str, confidence: str) -> str:
    notes = []
    if status == "draft":
        notes.append("⚠ 该案例尚未复核(draft),仅供参考")
    elif status == "verified":
        notes.append("✓ 已复核(verified)")
    if confidence in ("low", "medium"):
        notes.append(f"置信度 {confidence},建议结合实际验证")
    return " | ".join(notes)


# ----------------------- SQLite + FTS5 实现 -----------------------
class SqliteSearch(SearchBackend):
    def __init__(self, db_path: pathlib.Path = DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self._ok = None  # 缓存 FTS5 可用性探测结果

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        return conn

    def available(self) -> bool:
        """探测当前 sqlite3 是否支持 FTS5 + trigram(D1 同样支持)。不支持则上层回退到文件检索。"""
        if self._ok is None:
            try:
                c = sqlite3.connect(":memory:")
                c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
                c.close()
                self._ok = True
            except Exception:
                self._ok = False
        return self._ok

    # ---- 写入：单条 upsert（删后插，保证 cases / fts / signatures 三表一致）----
    def _upsert(self, conn: sqlite3.Connection, case: dict) -> None:
        cid = case["id"]
        row = conn.execute("SELECT rowid FROM cases WHERE id=?", (cid,)).fetchone()
        if row:
            rid = row[0]
            conn.execute("DELETE FROM cases_fts WHERE rowid=?", (rid,))
            conn.execute("DELETE FROM cases WHERE rowid=?", (rid,))
            conn.execute("DELETE FROM case_signatures WHERE case_id=?", (cid,))
        sigs = case.get("signatures") or []
        comps = case.get("components") or []
        cur = conn.execute(
            """INSERT INTO cases
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
            "INSERT INTO cases_fts(rowid, title, signatures_text, components, body) VALUES(?,?,?,?,?)",
            (rid, case.get("title", ""), "\n".join(sigs), "\n".join(comps), body),
        )
        for s in sigs:
            conn.execute("INSERT INTO case_signatures(case_id, signature) VALUES(?,?)", (cid, s))

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
            row = conn.execute("SELECT rowid FROM cases WHERE id=?", (case_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM cases_fts WHERE rowid=?", (row[0],))
                conn.execute("DELETE FROM cases WHERE rowid=?", (row[0],))
                conn.execute("DELETE FROM case_signatures WHERE case_id=?", (case_id,))
                conn.commit()
        finally:
            conn.close()

    def reindex_all(self) -> int:
        """从 wiki/cases/ 整库重建。文件是权威源,任何疑似不一致都可调它修复。"""
        if not self.available():
            return 0
        conn = self._connect()
        try:
            conn.execute("DELETE FROM cases_fts")
            conn.execute("DELETE FROM cases")
            conn.execute("DELETE FROM case_signatures")
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
        return conn.execute("SELECT count(*) FROM cases").fetchone()[0]

    def ensure_built(self) -> None:
        """索引为空但磁盘上有案例文件时,自动整库重建一次(首次启动 / CLI 直查的兜底)。"""
        if not self.available():
            return
        conn = self._connect()
        try:
            empty = self._count(conn) == 0
        finally:
            conn.close()
        if empty and any(case_from_file(p) for p in CASES_DIR.rglob("*.md")):
            self.reindex_all()

    # ---- 读取：精确命中(应用层子串) + FTS 模糊召回 ----
    def search(self, log: str, limit: int = 3) -> dict | None:
        if not self.available():
            return None
        import time
        started = time.perf_counter()
        self.ensure_built()
        log_low = log.lower()
        conn = self._connect()
        try:
            # 1) 精确命中:signature 作为子串出现在日志里(检索命门,最高优先级)
            sig_rows = conn.execute("SELECT case_id, signature FROM case_signatures").fetchall()
            matched: dict[str, list] = {}
            for cid, sig in sig_rows:
                if sig and sig.lower() in log_low:
                    matched.setdefault(cid, []).append(sig)
            if matched:
                hits = []
                for cid, sigs in matched.items():
                    r = conn.execute(
                        "SELECT title, file, status, confidence, solution FROM cases WHERE id=?",
                        (cid,)).fetchone()
                    if not r:
                        continue
                    title, file, status, confidence, solution = r
                    hits.append({
                        "title": title, "file": file, "matched": sigs,
                        "status": status, "confidence": confidence,
                        "note": _annotate(status, confidence),
                        "solution": solution or "(该案例无「解决方案」段落)",
                    })
                return self._done(started, {"mode": "exact", "hits": hits})

            # 2) 模糊召回:FTS5 trigram + bm25
            match_q = _fts_query(log)
            if match_q:
                try:
                    rows = conn.execute(
                        """SELECT c.title, c.file, c.status, bm25(cases_fts) AS score
                           FROM cases_fts JOIN cases c ON c.rowid = cases_fts.rowid
                           WHERE cases_fts MATCH ?
                           ORDER BY score ASC LIMIT ?""",
                        (match_q, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []           # 极端输入导致的 MATCH 语法问题不应让检索崩
                if rows:
                    hits = [{
                        "title": t, "file": f, "status": s,
                        "score": round(-score, 3),     # bm25 越小越相关 → 取负,越大越相关
                    } for (t, f, s, score) in rows]
                    return self._done(started, {"mode": "fuzzy", "hits": hits})

            # 3) 命中门控:无相关案例,绝不编造
            return self._done(started, {"mode": "none", "hits": []})
        finally:
            conn.close()

    @staticmethod
    def _done(started, payload: dict) -> dict:
        import time
        payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return payload


def _fts_query(log: str) -> str:
    """把任意日志文本转成安全的 FTS5 MATCH 查询。

    任意文本直接喂给 MATCH 会因引号/括号等触发 fts5 语法错误,这里抽取检索词、各自加引号
    当短语、用 OR 连接:
      - 英文词(>=3)、数字码(>=3):整词当短语(trigram 会做子串匹配)。
      - 中文片段:**切成 3 字滑窗**再 OR。否则一长串中文(如"服务的连接池…超时")会被当成
        单个短语,退化成"整串子串匹配",文档里没有这一整串就召不回;切窗后"连接池"等子片段
        能各自命中,再由 bm25 排序。
    """
    terms = []
    for tok in re.findall(r"[A-Za-z]{3,}|\d{3,}|[一-鿿]+", log):
        if "一" <= tok[0] <= "鿿":           # 中文串:3 字滑窗
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
        out.append('"' + t.replace('"', '""') + '"')   # 转义内部引号,作为短语
        if len(out) >= 60:                               # 限制词数,避免超长查询
            break
    return " OR ".join(out)


# 模块级单例,供 query.py / server.py 复用
backend: SearchBackend = SqliteSearch()


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "reindex":
        n = backend.reindex_all()
        logger.info("已从 wiki/cases/ 重建索引: %s 条案例 -> %s", n, DB_PATH)
    elif cmd == "search":
        if len(sys.argv) < 3:
            sys.exit('用法: python scripts/search_index.py search "报错文本"')
        logger.info(json.dumps(backend.search(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "stats":
        if not backend.available():
            sys.exit("当前 sqlite3 不支持 FTS5;检索会回退到文件扫描。")
        backend.ensure_built()
        conn = backend._connect()
        try:
            n = conn.execute("SELECT count(*) FROM cases").fetchone()[0]
            s = conn.execute("SELECT count(*) FROM case_signatures").fetchone()[0]
        finally:
            conn.close()
        logger.info("DB=%s\ncases=%s signatures=%s fts5_trigram=ok", DB_PATH, n, s)
    else:
        sys.exit("用法: python scripts/search_index.py [reindex|search <text>|stats]")


if __name__ == "__main__":
    _cli()
