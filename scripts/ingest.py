#!/usr/bin/env python3
"""
ingest.py — LLM Wiki 入库管线(三层结构版)

把一条原始排查记录:
  1) 原样存档到 raw/sources/(不可变层)
  2) 用 LLM 整理成结构化案例,落到 wiki/cases/_drafts/(待复核)
案例 frontmatter 的 sources 指回 raw/,保证可溯源。

用法:
    python ingest.py raw_note.txt --id INC-1234
    cat note.txt | python ingest.py - --id INC-1234

依赖: pip install pyyaml openai
凭证: 设置环境变量 OPENAI_API_KEY。
可选: INGEST_MODEL 覆盖模型(默认 gpt-4o);OPENAI_BASE_URL 指向兼容网关。
"""
import os, sys, json, re, argparse, datetime, pathlib, yaml
from openai import OpenAI

ROOT = pathlib.Path(__file__).resolve().parent.parent  # log-wiki/
RAW_DIR = ROOT / "raw" / "sources"
DRAFTS_DIR = ROOT / "wiki" / "cases" / "_drafts"

EXTRACT_PROMPT = """你是日志排查知识库的整理助手。把下面的原始排查记录整理成结构化案例。
**只输出 JSON**,不要额外文字。字段:
- title: 简短问题名
- category: 类别(数据库/内存/网络/中间件/配置 等)
- signatures: 字符串列表 —— 用户最可能粘贴的报错原文、异常类全名、错误码。
  【必须原文照搬,不得改写、翻译或概括】这是检索命中的命门。
- components: 涉及的服务/组件列表
- background / diagnosis / solution: 问题背景 / 定位过程 / 解决方案
信息缺失就留空字符串或空列表,绝不编造。
原始记录:
---
{raw}
---"""


MODEL = os.environ.get("INGEST_MODEL", "gpt-4o")
_client = None


def call_llm(prompt: str) -> str:
    """用 OpenAI 把原始记录整理成结构化案例,返回模型文本输出(纯 JSON)。

    凭证按 SDK 默认链解析:环境变量 OPENAI_API_KEY(及可选 OPENAI_BASE_URL)。
    用 response_format=json_object 强制 JSON 输出;signatures 原文照搬的约束写在 EXTRACT_PROMPT 里。
    """
    global _client
    if _client is None:
        _client = OpenAI()
    resp = _client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title.strip()).strip("-").lower()
    return (s or "case")[:50]


def archive_raw(raw: str, ident: str) -> pathlib.Path:
    """原样存档到不可变 raw/ 层。"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = RAW_DIR / f"{today}-{slugify(ident)}.md"
    path.write_text(raw, encoding="utf-8")
    return path


def extract(raw: str) -> dict:
    text = call_llm(EXTRACT_PROMPT.format(raw=raw)).strip()
    text = re.sub(r"^```json|```$", "", text, flags=re.M).strip()
    return json.loads(text)


def to_markdown(c: dict, raw_rel: str) -> str:
    fm = {
        "id": slugify(c["title"]),
        "title": c["title"],
        "category": c.get("category", "未分类"),
        "status": "draft",                       # 一律先 draft,待复核
        "confidence": "medium",
        "signatures": c.get("signatures", []),
        "components": c.get("components", []),
        "created": datetime.date.today().isoformat(),
        "sources": [raw_rel],                    # 溯源指回 raw/
    }
    front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    body = (
        f"## 问题背景\n{c.get('background','')}\n\n"
        f"## 定位过程\n{c.get('diagnosis','')}\n\n"
        f"## 解决方案\n{c.get('solution','')}\n"
    )
    return f"---\n{front}---\n\n{body}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="原始记录文件路径,或 - 表示从 stdin 读")
    ap.add_argument("--id", required=True, help="记录标识(如工单号),用于命名 raw 文件")
    args = ap.parse_args()

    raw = sys.stdin.read() if args.source == "-" else \
        pathlib.Path(args.source).read_text(encoding="utf-8")

    raw_path = archive_raw(raw, args.id)               # ① 存档不可变层
    raw_rel = str(raw_path.relative_to(ROOT))

    case = extract(raw)                                # ② LLM 结构化
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"{slugify(case['title'])}.md"
    out.write_text(to_markdown(case, raw_rel), encoding="utf-8")

    print(f"原始记录已存档: {raw_rel}")
    print(f"案例草稿已生成: {out.relative_to(ROOT)} (status=draft,复核后升 verified)")


if __name__ == "__main__":
    main()
