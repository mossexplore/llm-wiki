#!/usr/bin/env python3
"""
query.py — 用一段日志报错,从 wiki/cases/ 里找相似案例

策略(对应 SKILL.md 的 query 流程):
  1) 精确命中:遍历每个案例的 signatures,任一报错串作为子串出现在你的日志里 → 命中。
     (signatures 是知识库精选的检索锚点,比从日志里猜锚点更稳)
  2) 无精确命中 → 退化为 token 重合度模糊召回,仅作"可能相关"提示,标注需人工判断。
  3) 仍无 → 按命中门控明确告知"暂无相关案例",绝不编造。

用法:
    python query.py "把整段报错粘进来"
    cat error.log | python query.py -
"""
import sys, re, pathlib, time, logging

try:
    import search_index            # 与本文件同目录(backend/config.py 已把 scripts/ 加入 sys.path)
except ImportError:                # 直接 `python scripts/query.py` 时补一下路径
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import search_index

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "wiki" / "cases"
logger = logging.getLogger("log_wiki.query")


def _strip_comment(v: str) -> str:
    """去掉行内 YAML 注释( 空格+# 起)与首尾引号。"""
    return re.sub(r"\s+#.*$", "", v).strip().strip('"\'')


def _scalar(fm: str, key: str, default: str) -> str:
    """从 frontmatter 取一个标量字段(容忍行内注释)。"""
    m = re.search(rf"^{key}:[ \t]*(.+)$", fm, re.M)
    return _strip_comment(m.group(1)) if m else default


def _signatures(fm: str) -> list:
    """取 signatures 列表项:`signatures:`(可带行内注释)之后、下一个顶层 key 之前的 `- ...` 行。

    终止符用"下一个映射 key"(^\\w...:),而非"下一个非空行"——因为 PyYAML 生成的
    列表项是顶格的 `- item`,用非空行会被误当成边界,导致 signatures 解析为空。
    手写(缩进 `  - "..."`)与机器生成(顶格 `- item`)两种格式都能正确解析。
    """
    m = re.search(r"^signatures:[ \t]*(?:#[^\n]*)?\n(.*?)(?=^\w[\w-]*:|\Z)",
                  fm + "\n_end:", re.M | re.S)
    if not m:
        return []
    return [_strip_comment(s)
            for s in re.findall(r"^\s*-\s*(.+)$", m.group(1), re.M)]


def load_cases():
    """读取 wiki/cases/ 下所有案例(含 _drafts/),解析 frontmatter + 正文。零依赖。"""
    cases = []
    for path in sorted(CASES_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        _, fm, body = text.split("---", 2)
        cases.append({
            "path": path,
            "title": _scalar(fm, "title", path.stem),
            "status": _scalar(fm, "status", "unknown"),
            "confidence": _scalar(fm, "confidence", "unknown"),
            "signatures": _signatures(fm),
            "body": body,
        })
    return cases


def solution_of(body: str) -> str:
    """抽取「解决方案」段落(到下一个 ## 或文末)。"""
    m = re.search(r"##\s*解决方案\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    return m.group(1).strip() if m else "(该案例无「解决方案」段落)"


def tokenize(s: str):
    """切出英文单词(>=4 字母)与数字错误码,小写,用于模糊重合度。"""
    return {t.lower() for t in re.findall(r"[A-Za-z]{4,}|\b\d{3}\b", s)}


def annotate(c) -> str:
    """按 status/confidence 给可信度标注。"""
    notes = []
    if c["status"] == "draft":
        notes.append("⚠ 该案例尚未复核(draft),仅供参考")
    elif c["status"] == "verified":
        notes.append("✓ 已复核(verified)")
    if c["confidence"] in ("low", "medium"):
        notes.append(f"置信度 {c['confidence']},建议结合实际验证")
    return " | ".join(notes)


def search(log: str) -> dict:
    """检索核心:返回结构化结果,供 CLI 与 web 后端共用。

    返回 {"mode": "exact"|"fuzzy"|"none", "hits": [...], "elapsed_ms": int}。
    mode=exact 时 hits 含 solution;mode=fuzzy 时仅候选(需人工判断)。
    elapsed_ms 为本次检索的纯后端耗时(不含网络/序列化),单位毫秒。

    优先走 SQLite + FTS5 索引(模糊召回更准、文档多时更快);索引不可用
    (如 sqlite 未编译 FTS5)时,自动回退到下面的纯文件扫描,保证零依赖可用。
    """
    res = search_index.backend.search(log)
    if res is not None:
        return res
    return _search_files(log)


def _search_files(log: str) -> dict:
    """纯文件兜底实现:精确 signature 子串命中 → token 重合度模糊召回 → 无命中门控。"""
    log_low = log.lower()
    started = time.perf_counter()

    def _done(payload: dict) -> dict:
        payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    cases = load_cases()

    # 1) 精确命中:signature 作为子串出现在日志里
    exact = []
    for c in cases:
        matched = [s for s in c["signatures"] if s and s.lower() in log_low]
        if matched:
            exact.append({
                "title": c["title"],
                "file": str(c["path"].relative_to(ROOT)),
                "matched": matched,
                "status": c["status"],
                "confidence": c["confidence"],
                "note": annotate(c),
                "solution": solution_of(c["body"]),
            })
    if exact:
        return _done({"mode": "exact", "hits": exact})

    # 2) 模糊召回:token 重合度
    log_tokens = tokenize(log)
    scored = []
    for c in cases:
        sig_tokens = set().union(*(tokenize(s) for s in c["signatures"])) if c["signatures"] else set()
        score = len(log_tokens & (sig_tokens | tokenize(c["title"])))
        if score:
            scored.append({"title": c["title"], "file": str(c["path"].relative_to(ROOT)),
                           "score": score, "status": c["status"]})
    scored.sort(key=lambda x: -x["score"])
    if scored:
        return _done({"mode": "fuzzy", "hits": scored[:3]})

    # 3) 命中门控
    return _done({"mode": "none", "hits": []})


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    log = sys.stdin.read() if arg == "-" else arg
    if not log.strip():
        sys.exit("用法: python query.py \"报错信息\"  (或用 - 从 stdin 读)")
    if not load_cases():
        sys.exit("wiki/cases/ 下暂无任何案例。")

    res = search(log)
    if res["mode"] == "exact":
        lines = [f"=== 精确命中 {len(res['hits'])} 个案例 ===", ""]
        for h in res["hits"]:
            lines.extend([
                f"● {h['title']}",
                f"  文件: {h['file']}",
                f"  命中 signature: {h['matched']}",
            ])
            if h["note"]:
                lines.append(f"  可信度: {h['note']}")
            lines.extend(["", "  【解决方案】", _indent(h["solution"]), ""])
        logger.info("\n".join(lines))
    elif res["mode"] == "fuzzy":
        lines = ["=== 未精确命中。以下为可能相关案例(仅供参考,需人工判断,勿直接照搬)===", ""]
        for h in res["hits"]:
            lines.append(f"● {h['title']}  (重合度 {h['score']})  {h['file']}")
        lines.extend(["", "建议:用上面案例的 signatures 反向核对你的报错,或接入 QMD 语义检索。"])
        logger.info("\n".join(lines))
    else:
        logger.info("知识库中暂无相关案例。请勿编造解决方案;排查后可用 scripts/ingest.py 把本次结论入库。")


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines())


if __name__ == "__main__":
    main()
