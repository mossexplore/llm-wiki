# LLM Wiki

LLM Wiki 是一个面向日志排查、线上故障定位和运维知识沉淀的轻量级知识库。它把原始排查记录整理成**可追溯的 Markdown 知识**，并通过精确的错误特征串，帮助工程师在 Web 页面中找到已复核的解决方案。

知识统一按 **Google 的 Open Knowledge Format（OKF）规范** 组织：不可变的原始记录 + 结构化的 wiki 层，每条知识都是带类型化 YAML frontmatter 的 Markdown 文档，章节标准、交叉链接、可渐进式展开，既便于人读，也便于 agent 检索。

本项目的设计重点不是"自动生成更多内容"，而是"安全地沉淀可信经验"：模型抽取结果必须先预览和复核，原始记录会被归档留痕，检索无命中时明确返回无案例，避免编造解决方案。

## 功能特性

- Web 化知识流：写入、复核、列表、编辑、删除、检索、知识图谱、对话 Agent。
- 两步入库：LLM 先抽取预览，人工确认后才写入 verified 知识。
- 知识遵循 OKF 规范：类型化 frontmatter + 标准章节 + 交叉链接 + 渐进式目录。
- 原始记录归档到 `raw/sources/`，用于审计、复核和回溯。
- 以 `signatures` 为核心的精确检索，适合日志报错、异常类名、错误码和稳定错误片段。
- 支持批量写入：按 Markdown 一级标题 `# 标题` 拆分多条记录，并行抽取。
- 检索与知识列表本地运行，不依赖 OpenAI 或外部网络。
- 知识图谱展示 case、concept、tag、component、raw source 之间的关系。

## 项目结构

```text
.
├── config.example.yaml       # OpenAI 兼容接口配置模板
├── db/                       # 检索索引表结构与说明书（SQLite/D1、MySQL DDL）
├── index/                    # 自动生成的 SQLite 检索索引（gitignore，可重建）
├── operations/               # 维护流程说明
├── raw/sources/              # 入库时生成的不可变原始记录
├── scripts/                  # Web 后端复用的内部模块，无需手动运行
│   ├── search_index.py       # 检索索引后端（SQLite + FTS5，模糊召回）
│   ├── agent.py              # 对话 Agent：先检索 wiki，未命中再调大模型兜底
│   └── chat_store.py         # 对话运营数据持久化（会话/消息/点赞点踩，SQLite）
├── server/
│   ├── server.py             # FastAPI 后端
│   └── static/               # 单页前端（index.html + css/ + js/，含对话页 chat.js）
├── wiki/
│   ├── cases/                # 故障案例，signatures 是检索锚点
│   ├── concepts/             # 跨案例的通用排查规律
│   └── index.md              # 自动生成的 OKF 渐进式目录
└── requirements.txt
```

## 环境要求

- 推荐 Python 3.10 或更新版本。
- 只有"写入知识"的 LLM 抽取步骤需要 OpenAI 兼容 API key。
- 检索、列表、编辑已有案例和图谱生成都是本地文件操作。

## 快速开始

1. 创建虚拟环境并安装依赖：

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 创建本地配置文件：

   ```bash
   cp config.example.yaml config.yaml
   ```

   编辑 `config.yaml`：

   ```yaml
   env: "dev"
   openai:
     api_key: "sk-..."
     base_url: "https://api.openai.com/v1"
     model: "gpt-4o"
   ```

   `base_url` 可选，可用于代理、Azure 或本地 OpenAI 兼容网关；`model` 未配置时默认使用 `gpt-4o`。

3. 在仓库根目录启动 Web 服务：

   ```bash
   uvicorn server.server:app --reload --port 8000
   ```

   浏览器打开 <http://127.0.0.1:8000/>。

知识写入、复核、列表、检索、编辑、删除和图谱查看都在 Web 页面中完成，无需手动执行 Python 脚本。

## Web 使用流程

### 写入知识

粘贴一段原始排查记录后，后端会流式调用模型抽取结构化 JSON。页面会展示 `title`、`category`、`signatures`、`components`、`background`、`diagnosis`、`solution` 等字段，供用户复核和修改。

预览阶段不会写任何文件；只有点击确认入库后，才会同时写入：

- `raw/sources/`：原始记录，作为不可变溯源材料。
- `wiki/cases/`：已复核的 verified 案例。

批量写入时，上传或粘贴的 Markdown 会按一级标题拆分，每条记录独立抽取、独立展示、独立确认：

```markdown
# 日志 A
...

# 日志 B
...
```

### 知识列表

知识列表展示 `wiki/cases/` 下的 verified 案例，并显示入库时间。可以打开单条知识查看详情、编辑字段、覆盖更新 Markdown，或删除案例文件。删除只移除 `wiki/cases/*.md`，不会删除 `raw/sources/` 中的原始记录。

### 检索知识

粘贴日志、堆栈、错误码或故障现象后，检索流程如下，并展示检索耗时（毫秒）：

1. **精确命中**：如果某个案例的 `signatures` 出现在输入文本中，直接返回命中案例和解决方案。这是检索命门，优先级最高。
2. **模糊候选**：精确命中失败时，用 **SQLite + FTS5（BM25 全文检索、trigram 中文分词）** 返回可能相关案例；文档变多时依然又快又准。
3. **无命中门控**：没有相关案例时明确返回暂无案例，不编造答案。

检索索引由 `wiki/cases/*.md` 派生（Markdown 始终是权威源），入库/更新/删除时自动同步、服务启动时整库重建，可随时用 `python scripts/search_index.py reindex` 重建。FTS5 不可用时自动回退到纯文件扫描，功能不变。索引表结构、查询示例与迁移（D1 / MySQL）说明见 [db/README.md](db/README.md)。

### 知识图谱

图谱页根据 Markdown frontmatter 和正文链接构建节点与边，展示 case、concept、raw source、tag、component、citation、related link 和相似案例关系。

### 对话 Agent

对话页参考 NextChat 的布局：左侧管理会话（新建 / 切换 / 删除），右侧是与 Agent 的多轮交互。回答遵循「先检索、后兜底」：

1. **先检索知识库**：用 `/api/query` 的同一套检索逻辑找相关案例。
   - **精确命中**（signature 原文匹配）→ 直接用命中案例的解决方案流式回答，并标注**来源 wiki**。
   - **模糊命中且关联度足够大**（BM25 相关度 ≥ 阈值 `CHAT_FUZZY_THRESHOLD`，默认 1.0）→ 用最相关案例的解决方案回答，标注来源 wiki 并提示需人工判断。
2. **检索不到 / 关联度太小** → 明确说明知识库未命中，转而调用大模型（`config.yaml` 里的 OpenAI 兼容接口）**流式**回答。
3. 每条 Agent 回复都能**复制、点赞、点踩**（点踩弹窗要求填写原因）。

所有会话、用户提问、Agent 回复、点赞 / 点踩（含原因）都落库到 `db/chat.db`（运行库，已 gitignore），表结构见 [db/schema.chat.sql](db/schema.chat.sql)，便于后续做对话质量分析、知识盲区发现和答案来源统计。

## 知识格式（Open Knowledge Format）

知识严格按 **Google 的 Open Knowledge Format（OKF）规范** 组织。核心约定：

- **类型化 frontmatter**：每条知识用 YAML frontmatter 声明 `id` / `type` / `title` / `tags` / `status` 等元数据。
- **标准章节**：正文用固定的 Markdown 标题分段（问题背景 / 定位过程 / 解决方案 / Citations）。
- **交叉链接与引用**：通过 `sources`、`related` 和正文中的 Markdown 链接建立可追溯的引用关系。
- **渐进式目录**：`wiki/index.md` 自动生成 OKF 风格的渐进式索引，便于人和 agent 逐层展开。

每个 case 是带 YAML frontmatter 的 Markdown 文件：

```markdown
---
id: hikari-pool-timeout
type: Incident Case
title: HikariPool 连接池耗尽致接口批量 500
description: 慢查询长期占用连接，导致连接池耗尽并引发接口 500。
category: 数据库 / 连接池
tags:
  - database
  - hikari
status: verified
confidence: high
signatures:
  - HikariPool-1 - Connection is not available, request timed out
components:
  - order-service
  - HikariCP
  - MySQL
sources:
  - raw/sources/2026-06-16-inc-1234.md
---

## 问题背景
...

## 定位过程
...

## 解决方案
...

## Citations

[1] [原始排查记录](/raw/sources/2026-06-16-inc-1234.md)
```

关键约定：

- `signatures` 必须保留用户最可能粘贴的原始错误文本，不翻译、不改写、不概括。
- `sources` 必须指回对应的 raw 原始记录。
- `status: verified` 表示已复核，可作为回答依据。
- `status: draft` 表示尚未复核，引用时必须标注风险。

## API 一览

前端使用以下 FastAPI 接口：

| 方法     | 路径                           | 说明                                            |
| -------- | ------------------------------ | ----------------------------------------------- |
| `POST`   | `/api/ingest/preview`          | 流式抽取单条记录，仅预览，不落库。              |
| `POST`   | `/api/ingest/preview_batch`    | 按一级标题拆分 Markdown，以 NDJSON 流式返回批量抽取事件。 |
| `POST`   | `/api/ingest/commit`           | 确认写入一条已复核案例。                        |
| `POST`   | `/api/ingest/commit_batch`     | 批量确认写入多条案例。                          |
| `GET`    | `/api/knowledge`               | 获取 verified 知识列表。                        |
| `GET`    | `/api/knowledge/{case_file}`   | 获取单条知识详情和原始记录。                    |
| `PUT`    | `/api/knowledge/{case_file}`   | 更新单条知识并刷新索引。                        |
| `DELETE` | `/api/knowledge/{case_file}`   | 删除单条知识，保留 raw 原始记录。               |
| `POST`   | `/api/query`                   | 按日志或报错信息检索知识，返回检索耗时（毫秒）。 |
| `GET`    | `/api/graph`                   | 返回知识图谱节点和边。                          |
| `GET`    | `/api/kb/stats`                | 返回案例数、草稿数、signature 数和更新时间。    |
| `GET`    | `/api/examples/ingest`         | 返回一条示例原始记录和示例结构化案例。          |
| `POST`   | `/api/chat/sessions`           | 新建对话会话。                                  |
| `GET`    | `/api/chat/sessions`           | 列出全部会话（按最近活跃排序）。                |
| `GET`    | `/api/chat/sessions/{id}/messages` | 获取某会话的全部消息（含反馈）。            |
| `DELETE` | `/api/chat/sessions/{id}`      | 删除会话及其消息、反馈。                        |
| `POST`   | `/api/chat/sessions/{id}/messages` | 发送提问，先检索后大模型兜底，NDJSON 流式返回回答。 |
| `POST`   | `/api/chat/messages/{id}/feedback` | 对某条 Agent 回复点赞 / 点踩（点踩需带原因）。 |

## 配置说明

入库管线默认读取仓库根目录的 `config.yaml`。

不要提交真实 API key。公开仓库建议只提交 `config.example.yaml`，把 `config.yaml`、私有网关地址和密钥放在本地私有配置中。

## 质量护栏

本项目默认采用保守模式，优先保证排查结论可信。

- **预览后写入**：模型抽取结果不会直接修改知识库。
- **保留原始签名**：`signatures` 是检索命门，不能被改写。
- **原始记录不可变**：每个案例都应能追溯到 raw source。
- **不自动合并案例**：跨案例概念和归纳需要显式复核。
- **不编造解决方案**：无命中就返回无命中，不能根据相似经验伪造答案。

## 开发

本项目使用 FastAPI 和静态前端，没有前端构建步骤。启动开发服务：

```bash
uvicorn server.server:app --reload --port 8000
```

## 贡献指南

欢迎贡献。适合本项目的改进包括：

- 更安全的写入、复核和回滚流程。
- 在不破坏无命中门控的前提下提升检索质量。
- 更完整的 wiki 健康检查。
- 更清晰、可审计的 Web UI。
- 文档、示例和测试用例。

提交变更前建议：

1. 确认 `uvicorn server.server:app --port 8000` 能正常启动。
2. 通过 Web 页面验证写入、列表、检索和图谱等核心流程。
3. 不提交密钥、私有日志、客户数据或未经脱敏的原始事故记录。
4. 保证新增知识都能追溯到 raw source。

## 安全与隐私

原始排查记录可能包含凭证、客户数据、主机名、IP、内部服务名或专有实现细节，公开仓库前请先脱敏。

使用 LLM 抽取时，原始记录会发送到 `config.yaml` 中配置的 OpenAI 兼容接口。请根据组织安全要求选择模型提供方、网关和脱敏流程。

## 许可证

本项目使用 MIT License 开源。你可以在遵守 MIT License 条款的前提下自由使用、复制、修改、合并、发布、分发、再许可和销售本项目副本。
