# log-wiki —— 日志排查知识库(LLM Wiki 形态)

按 Karpathy 的 LLM Wiki 模式构建的、面向 agent 的日志故障定位知识库。
针对"排查领域正确性要求高"做了裁剪:保守入库 + 两道护栏,而非研究版的激进综合。

**全部操作通过 Web 界面完成**(写入 / 列表 / 检索 / 图谱),无需命令行。

## 三层结构

```
log-wiki/
├── SKILL.md                 schema 层:结构定义 + query 检索规则 + 维护操作入口(agent 读这个)
├── raw/                     第一层:不可变原始记录,永不修改,综合出错时回溯
│   └── sources/
│       └── 2024-05-10-INC-1234.md
├── wiki/                    第二层:LLM 生成的结构化知识(单点真相源,agent 检索此层)
│   ├── cases/               具体故障案例(三段式 + frontmatter,signatures 为检索锚点)
│   │   ├── index.md         OKF 风格目录索引,入库后自动刷新
│   │   ├── db-connection-timeout.md
│   │   └── _drafts/         入库草稿暂存(待复核)
│   └── concepts/            跨案例综合的通用规律(辅助直觉,不替代具体案例)
│       ├── index.md         OKF 风格目录索引
│       └── connection-pool-exhaustion.md
├── operations/              操作定义(ingest / lint 的规则说明)
├── scripts/                 Web 后端复用的内部模块(入库 / 检索 / 图谱;非手动使用)
├── server/
│   ├── server.py            FastAPI 后端:写入/列表/更新/删除/检索/图谱接口
│   └── static/              单页前端(index.html 外壳 + css/ + js/,按页面拆分)
├── config.example.yaml      OpenAI 配置模板
└── requirements.txt         依赖(pyyaml + openai + fastapi + uvicorn)
```

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # 填入本地 OpenAI api_key(仅「写入知识」的抽取步骤需要)
uvicorn server.server:app --port 8000   # 在仓库根目录运行
# 浏览器打开 http://127.0.0.1:8000/
```

> 配置只放本地 `config.yaml`(已被 `.gitignore` 排除,不会提交);可选 `base_url`(走代理/Azure/本地兼容网关)与 `model`(默认 gpt-4o)。

## 四个页面

- **① 写入知识**:粘贴原始排查记录 → 模型**流式**抽取成结构化案例 → 在页面上复核/修改(尤其 signatures)→ 点「确认入库」才真正写入 `wiki/cases/`(护栏①:确认前不碰知识库)。上传 Markdown 批量写入时,按一级标题 `# 标题` 切分记录,会先确认切分结果再并行抽取;每条记录都能看到对应的大模型流式输出。
- **② 知识列表**:展示已入库知识,带入库时间;入库后切到本页自动刷新。右侧复用复核表单查看/编辑详情,「确认更新」覆盖对应 Markdown 并刷新索引、保留 `sources` 与额外章节;支持**删除**(原文 `raw/` 保留以备溯源)。
- **③ 检索知识**:粘一段日志报错(带时间戳/毫秒数等噪声没关系)→ 返回精确命中的「解决方案」、可能相关候选、或"暂无案例"门控(绝不编造)。
- **④ 知识图谱**:从 cases / concepts / tags / components / raw sources 构建可拖拽图谱,查看案例、组件、标签与原始记录之间的关系。

> 检索与知识列表不依赖 OpenAI,只有「写入知识」的抽取步骤才调模型。

## 两道护栏(排查领域必备)

1. **draft → verified 需复核**:模型抽取一律先预览;只有用户在页面确认后才落库为 verified。一条错误的归档解法可能直接引发事故,这道闸不能省。
2. **signatures 原文不可改**:检索全靠精确报错串命中,抽取/编辑阶段都不得改写 signatures。

## 接口一览(前端调用,通常无需直接使用)

- `POST /api/ingest/preview` — 流式抽取 JSON,仅预览,不落库。
- `POST /api/ingest/preview_batch` — 批量并行流式抽取,以 NDJSON 返回每条记录的 start/delta/done/summary 事件。
- `POST /api/ingest/commit` — 确认入库,写 `raw/sources/` 与 `wiki/cases/`。
- `POST /api/ingest/commit_batch` — 批量确认入库,逐条写入并汇总结果。
- `GET /api/knowledge` — 已入库知识列表(含入库时间)。
- `GET /api/knowledge/{case_file}` — 读取单条知识详情。
- `PUT /api/knowledge/{case_file}` — 更新单条知识并刷新索引。
- `DELETE /api/knowledge/{case_file}` — 删除单条知识(`raw/` 原文保留)。
- `POST /api/query` — 按日志报错检索知识。
- `GET /api/graph` — 构建知识图谱数据。

## 检索层:QMD(可选增强)

当你只有"症状描述"而非精确报错串时,默认的 signatures 反向匹配会漏。可接入 QMD(本地 markdown 搜索,BM25/向量混合 + LLM 重排,带 MCP server)作语义检索层:将其指向本仓库 `wiki/` 目录、把 MCP server 注册到你的 agent 即可;新增案例 QMD 自动增量索引,无需手工重建。

## 给 agent 使用

把 `SKILL.md` 装进你的 agent,并在系统提示里写死"凡日志/报错/故障问题必须先查本知识库"。agent 会按 SKILL.md 的 query 流程检索 `wiki/cases/` 并依「解决方案」作答,无命中明确告知、不编造。

## 整体闭环

```
粘贴排查记录 ──写入页──▶ 流式抽取 → 人工复核确认 ──▶ raw/(原文存档) + wiki/cases/(verified)
                                                              │
贴报错检索 ◀──检索页 / agent / QMD───────────────────────────┘
                    无命中 → 明说"暂无案例" → 排查后回到写入页,形成闭环
```
