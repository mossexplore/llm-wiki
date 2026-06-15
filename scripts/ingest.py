#!/usr/bin/env python3
"""
ingest.py — LLM Wiki 入库管线(三层结构版)

把一条原始排查记录:
  1) 原样存档到 raw/sources/(不可变层)
  2) 用 LLM 整理成结构化案例,落到 wiki/cases/_drafts/(待复核)
案例 frontmatter 的 sources 指回 raw/,保证可溯源。

用法:
    python ingest.py raw_note.txt              # --id 缺省,用时间戳自动命名
    python ingest.py raw_note.txt --id INC-1234  # 也可手动指定(如有工单号)
    cat note.txt | python ingest.py -

依赖: pip install pyyaml openai
配置: config.yaml / config.example.yaml 提供本地样例;也可用 INGEST_CONFIG 指定其它路径。
"""
import os, sys, json, re, argparse, datetime, logging, pathlib, yaml
from openai import OpenAI

ROOT = pathlib.Path(__file__).resolve().parent.parent  # log-wiki/
RAW_DIR = ROOT / "raw" / "sources"
DRAFTS_DIR = ROOT / "wiki" / "cases" / "_drafts"
logger = logging.getLogger("log_wiki.ingest")
logger.setLevel(logging.INFO)

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


CONFIG_PATH = pathlib.Path(os.environ.get("INGEST_CONFIG", ROOT / "config.yaml"))
_client = None
_model = None


def load_config() -> dict:
    """读取本地 config.yaml 的 openai 段:api_key / base_url / model。

    缺失时抛 RuntimeError(普通异常,便于 Web 端 except Exception 捕获并流式回传);
    CLI 侧在 main() 里把它转成友好退出。
    """
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"缺少配置文件 {CONFIG_PATH};请复制 config.example.yaml 为 config.yaml 并填写。")
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    env = data.get("env", "dev")
    if env == "dev":
        os.environ["NO_PROXY"] = "127.0.0.1"
    cfg = data.get("openai", {})
    if not cfg.get("api_key"):
        raise RuntimeError(f"配置文件 {CONFIG_PATH} 缺少 openai.api_key。")
    logger.info(
        "ingest.config.loaded env=%s config_path=%s base_url=%s model=%s no_proxy=%s",
        env, CONFIG_PATH, cfg.get("base_url") or "<default>",
        cfg.get("model", "gpt-4o"), os.environ.get("NO_PROXY", ""),
    )
    return cfg


def _client_and_model():
    """懒加载 OpenAI 客户端;URL/模型/密钥全部来自本地 config.yaml(见 load_config)。"""
    global _client, _model
    if _client is None:
        cfg = load_config()
        _model = cfg.get("model", "gpt-4o")
        _client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)
    return _client, _model


def call_llm(prompt: str) -> str:
    """一次性返回:把原始记录整理成结构化案例(纯 JSON)。CLI 入库用。

    用 response_format=json_object 强制 JSON;signatures 原文照搬的约束写在 EXTRACT_PROMPT 里。
    """
    client, model = _client_and_model()
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def stream_llm(prompt: str):
    """流式返回:逐段 yield 模型输出文本(增量)。Web 端实时展示用。"""
    client, model = _client_and_model()
    stream = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


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


def to_markdown(c: dict, raw_rel: str, status: str = "draft",
                confidence: str = "medium") -> str:
    fm = {
        "id": slugify(c["title"]),
        "title": c["title"],
        "category": c.get("category", "未分类"),
        "status": status,                        # CLI 默认 draft;web 端复核确认后传 verified
        "confidence": confidence,
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
    ap.add_argument("--id", default=None,
                    help="记录标识(如工单号),用于命名 raw 文件;不传则用时间戳自动生成")
    args = ap.parse_args()

    # 没有工单号系统时,用 时分秒 自动生成标识,保证同一天多次入库不重名
    ident = args.id or datetime.datetime.now().strftime("%H%M%S")

    raw = sys.stdin.read() if args.source == "-" else \
        pathlib.Path(args.source).read_text(encoding="utf-8")

    raw_path = archive_raw(raw, ident)                 # ① 存档不可变层
    raw_rel = str(raw_path.relative_to(ROOT))

    try:
        case = extract(raw)                            # ② LLM 结构化
    except RuntimeError as e:                           # 配置缺失等,友好退出
        sys.exit(str(e))
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"{slugify(case['title'])}.md"
    out.write_text(to_markdown(case, raw_rel), encoding="utf-8")

    print(f"原始记录已存档: {raw_rel}")
    print(f"案例草稿已生成: {out.relative_to(ROOT)} (status=draft,复核后升 verified)")


if __name__ == "__main__":
    main()
