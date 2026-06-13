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
import sys, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "wiki" / "cases"


def _strip_comment(v: str) -> str:
    """去掉行内 YAML 注释( 空格+# 起)与首尾引号。"""
    return re.sub(r"\s+#.*$", "", v).strip().strip('"\'')


def _scalar(fm: str, key: str, default: str) -> str:
    """从 frontmatter 取一个标量字段(容忍行内注释)。"""
    m = re.search(rf"^{key}:[ \t]*(.+)$", fm, re.M)
    return _strip_comment(m.group(1)) if m else default


def _signatures(fm: str) -> list:
    """取 signatures 列表项:`signatures:`(可带行内注释)之后、下一个顶层 key 之前的 `- "..."` 行。"""
    m = re.search(r"^signatures:[ \t]*(?:#[^\n]*)?\n(.*?)(?=^\S)", fm + "\n_:", re.M | re.S)
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


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    log = sys.stdin.read() if arg == "-" else arg
    if not log.strip():
        sys.exit("用法: python query.py \"报错信息\"  (或用 - 从 stdin 读)")

    log_low = log.lower()
    cases = load_cases()
    if not cases:
        sys.exit("wiki/cases/ 下暂无任何案例。")

    # 1) 精确命中:signature 作为子串出现在日志里
    hits = []
    for c in cases:
        matched = [s for s in c["signatures"] if s and s.lower() in log_low]
        if matched:
            hits.append((c, matched))

    if hits:
        print(f"=== 精确命中 {len(hits)} 个案例 ===\n")
        for c, matched in hits:
            print(f"● {c['title']}")
            print(f"  文件: {c['path'].relative_to(ROOT)}")
            print(f"  命中 signature: {matched}")
            note = annotate(c)
            if note:
                print(f"  可信度: {note}")
            print(f"\n  【解决方案】\n{_indent(solution_of(c['body']))}\n")
        return

    # 2) 模糊召回:token 重合度
    log_tokens = tokenize(log)
    scored = []
    for c in cases:
        sig_tokens = set().union(*(tokenize(s) for s in c["signatures"])) if c["signatures"] else set()
        score = len(log_tokens & (sig_tokens | tokenize(c["title"])))
        if score:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])

    if scored:
        print("=== 未精确命中。以下为可能相关案例(仅供参考,需人工判断,勿直接照搬)===\n")
        for score, c in scored[:3]:
            print(f"● {c['title']}  (重合度 {score})  {c['path'].relative_to(ROOT)}")
        print("\n建议:用上面案例的 signatures 反向核对你的报错,或接入 QMD 语义检索。")
        return

    # 3) 命中门控
    print("知识库中暂无相关案例。请勿编造解决方案;排查后可用 scripts/ingest.py 把本次结论入库。")


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines())


if __name__ == "__main__":
    main()
