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
WIKI_DIR = ROOT / "wiki"
CASES_DIR = WIKI_DIR / "cases"
logger = logging.getLogger("log_wiki.ingest")
logger.setLevel(logging.INFO)

EXTRACT_PROMPT = """你是日志排查知识库的整理助手。把下面的原始排查记录整理成结构化案例。
**只输出 JSON**,不要额外文字。字段:
- title: 简短问题名
- description: 单句摘要,用于索引和检索预览
- category: 类别(内存/网络/异常退出/训练卡住 等)
- tags: 字符串列表,短标签,如 database / hikari / timeout
- signatures: 字符串列表 —— 用户最可能粘贴的报错原文、异常类全名、错误码。
  【必须原文照搬,不得改写、翻译或概括】这是检索命中的命门。
- components: 涉及的服务/组件列表
- background / diagnosis / solution: 问题背景（或问题现象） / 定位过程 / 解决方案（排查步骤）
信息缺失就留空字符串或空列表,绝不编造。特别是定位过程可能为空，不要编造定位过程。
原始记录:
---
{raw}
---"""


CONFIG_PATH = pathlib.Path(os.environ.get("INGEST_CONFIG", ROOT / "config.yaml"))
_client = None
_model = None


def _description(c: dict) -> str:
    desc = (c.get("description") or "").strip()
    if desc:
        return desc
    for key in ("background", "diagnosis", "solution"):
        value = (c.get(key) or "").strip()
        if value:
            return re.sub(r"\s+", " ", value)[:120]
    return c.get("title", "")


def _tags(c: dict) -> list:
    tags = c.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    category = c.get("category")
    if category:
        tags.append(category)
    tags.extend(c.get("components") or [])
    seen = set()
    out = []
    for tag in tags:
        tag = str(tag).strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out


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
    if not getattr(resp, "choices", None):     # 网关报错时 choices 可能为 None
        raise RuntimeError(f"模型未返回 choices(网关或鉴权异常):{resp}")
    return (resp.choices[0].message.content or "")


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
    # 走流式累加(与 Web 单条预览同一通路;部分网关只在 stream=True 时返回正常 choices)
    text = "".join(stream_llm(EXTRACT_PROMPT.format(raw=raw))).strip()
    text = re.sub(r"^```json|```$", "", text, flags=re.M).strip()
    if not text:
        raise RuntimeError("模型返回为空")
    data = json.loads(text)
    # 模型有时会把案例包成 [{...}] 或 {"cases":[{...}]};归一化为单个对象
    if isinstance(data, dict) and len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, list):
            data = only
    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict)), {})
    if not isinstance(data, dict):
        raise RuntimeError(f"模型未返回 JSON 对象,实际为 {type(data).__name__}")
    return data


def to_markdown(c: dict, raw_rel: str, status: str = "draft",
                confidence: str = "medium") -> str:
    today = datetime.date.today().isoformat()
    fm = {
        "id": slugify(c["title"]),
        "type": c.get("type", "Incident Case"),
        "title": c["title"],
        "description": _description(c),
        "category": c.get("category", "未分类"),
        "tags": _tags(c),
        "status": status,                        # CLI 默认 draft;web 端复核确认后传 verified
        "confidence": confidence,
        "signatures": c.get("signatures", []),
        "components": c.get("components", []),
        "created": today,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "sources": [raw_rel],                    # 溯源指回 raw/
    }
    front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    body = (
        f"## 问题背景\n{c.get('background','')}\n\n"
        f"## 定位过程\n{c.get('diagnosis','')}\n\n"
        f"## 解决方案\n{c.get('solution','')}\n\n"
        f"## Citations\n\n"
        f"[1] [原始排查记录](/{raw_rel})\n"
    )
    return f"---\n{front}---\n\n{body}"


def _read_frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    try:
        _, fm, _ = text.split("---", 2)
    except ValueError:
        return {}
    return yaml.safe_load(fm) or {}


def _index_entry(path: pathlib.Path, base: pathlib.Path) -> str:
    fm = _read_frontmatter(path)
    title = fm.get("title") or path.stem
    description = fm.get("description") or fm.get("category") or fm.get("type") or ""
    rel = path.relative_to(base).as_posix()
    suffix = f" - {description}" if description else ""
    return f"* [{title}]({rel}){suffix}"


def update_indexes() -> None:
    """生成 OKF 风格 index.md,用于渐进式浏览 wiki bundle。"""
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    CASES_DIR.mkdir(parents=True, exist_ok=True)

    case_paths = sorted(p for p in CASES_DIR.glob("*.md") if p.name not in ("index.md", "log.md"))
    concept_dir = WIKI_DIR / "concepts"
    concept_paths = sorted(concept_dir.glob("*.md")) if concept_dir.exists() else []
    concept_paths = [p for p in concept_paths if p.name not in ("index.md", "log.md")]

    case_lines = [_index_entry(path, CASES_DIR) for path in case_paths]
    (CASES_DIR / "index.md").write_text(
        "# 故障案例\n\n"
        "已复核或待复核的单次故障案例。每个案例保留 signatures、sources 与解决方案。\n\n"
        + ("\n".join(case_lines) if case_lines else "_暂无案例。_")
        + "\n",
        encoding="utf-8",
    )

    root_parts = [
        "# log-wiki 知识目录",
        "",
        "这是一个 OKF-ish knowledge bundle: Markdown 文件 + YAML frontmatter + 普通 Markdown 链接。",
        "",
        "## Cases",
        "",
        "* [故障案例](cases/) - 单次事故记录,以 signatures 作为检索锚点。",
    ]
    if concept_paths:
        root_parts.extend([
            "",
            "## Concepts",
            "",
            "* [通用概念](concepts/) - 跨案例综合出的排查规律。",
        ])
    (WIKI_DIR / "index.md").write_text("\n".join(root_parts) + "\n", encoding="utf-8")

    if concept_dir.exists():
        concept_lines = [_index_entry(path, concept_dir) for path in concept_paths]
        (concept_dir / "index.md").write_text(
            "# 通用概念\n\n"
            "跨案例综合出的排查规律。概念页只辅助建立直觉,具体作答仍以 case 为准。\n\n"
            + ("\n".join(concept_lines) if concept_lines else "_暂无概念。_")
            + "\n",
            encoding="utf-8",
        )


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
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
    update_indexes()

    logger.info("原始记录已存档: %s", raw_rel)
    logger.info("案例草稿已生成: %s (status=draft,复核后升 verified)", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
