# WiseRec Wiki

WiseRec Wiki 是一个面向日志排查、线上故障定位和运维知识沉淀的轻量级 LLM Wiki。它把原始排查记录整理成可追溯的 Markdown 知识库，并通过精确的错误特征串帮助工程师或 agent 找到已复核的解决方案。

本项目的设计重点不是“自动生成更多内容”，而是“安全地沉淀可信经验”：模型抽取结果必须先预览和复核，原始记录会被归档留痕，检索无命中时明确返回无案例，避免编造解决方案。

## 功能特性

- Web 化知识流：写入、复核、列表、编辑、删除、检索、知识图谱。
- 两步入库：LLM 先抽取预览，人工确认后才写入 verified 知识。
- 原始记录归档到 `raw/sources/`，用于审计、复核和回溯。
- 结构化 Markdown 知识库：`wiki/` 使用 YAML frontmatter 和普通 Markdown 链接。
- 以 `signatures` 为核心的精确检索，适合日志报错、异常类名、错误码和稳定错误片段。
- 支持批量写入：按 Markdown 一级标题 `# 标题` 拆分多条记录。
- 检索与知识列表本地运行，不依赖 OpenAI 或外部网络。
- 知识图谱展示 case、concept、tag、component、raw source 之间的关系。
- 提供面向 agent 的 `SKILL.md`，定义检索规则、知识 schema 和维护护栏。

## 项目结构

```text
.
├── SKILL.md                  # agent 读取的知识库说明与检索规则
├── config.example.yaml       # OpenAI 兼容接口配置模板
├── operations/               # ingest / lint 操作说明
├── raw/sources/              # 入库时生成的不可变原始记录
├── scripts/
│   ├── graph.py              # 从 wiki 文档构建知识图谱 JSON
│   ├── ingest.py             # 命令行入库管线
│   ├── lint_okf.py           # 知识库健康检查
│   └── query.py              # 命令行和后端共用的检索逻辑
├── server/
│   ├── server.py             # FastAPI 后端
│   └── static/               # 单页前端
├── wiki/
│   ├── cases/                # 故障案例，signatures 是检索锚点
│   ├── concepts/             # 跨案例的通用排查规律
│   └── index.md              # 自动生成的渐进式目录
└── requirements.txt
```

## 环境要求

- 推荐 Python 3.10 或更新版本。
- 只有“写入知识”的 LLM 抽取步骤需要 OpenAI 兼容 API key。
- 检索、列表、编辑已有案例、lint 和图谱生成都是本地文件操作。

## 快速开始

1. 创建虚拟环境并安装依赖。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 创建本地配置文件。

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

`base_url` 可选，可用于代理、Azure 或本地 OpenAI 兼容网关；`model` 未配置时，代码默认使用 `gpt-4o`。

3. 在仓库根目录启动 Web 服务。

```bash
uvicorn server.server:app --reload --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。

## Web 使用流程

### 写入知识

粘贴一段原始排查记录后，后端会流式调用模型抽取结构化 JSON。页面会展示 title、category、signatures、components、background、diagnosis、solution 等字段，用户可以复核和修改。

预览阶段不会写任何文件；只有点击确认入库后，才会同时写入：

- `raw/sources/`：原始记录，作为不可变溯源材料。
- `wiki/cases/`：已复核的 verified 案例。

批量写入时，上传或粘贴的 Markdown 会按一级标题拆分：

```markdown
# 事故 A
...

# 事故 B
...
```

每条记录独立抽取、独立展示、独立确认。

### 知识列表

知识列表展示 `wiki/cases/` 下的 verified 案例。可以打开单条知识查看详情、编辑字段、覆盖更新 Markdown，或删除案例文件。删除只移除 `wiki/cases/*.md`，不会删除 `raw/sources/` 中的原始记录。

### 检索知识

粘贴日志、堆栈、错误码或故障现象后，检索流程如下：

1. 精确命中：如果某个案例的 `signatures` 出现在输入文本中，直接返回命中案例和解决方案。
2. 模糊候选：精确命中失败时，基于 token 重合度返回可能相关案例。
3. 无命中门控：没有相关案例时明确返回暂无案例，不编造答案。

### 知识图谱

图谱页根据 Markdown frontmatter 和正文链接构建节点与边，展示 case、concept、raw source、tag、component、citation、related link 和相似案例关系。

## 命令行用法

### 写入单条记录

命令行入库会把原始输入保存到 `raw/sources/`，调用模型抽取结构化案例，并写入 `wiki/cases/_drafts/`。

```bash
python3 scripts/ingest.py path/to/incident-note.md --id INC-1234
```

也可以从标准输入读取：

```bash
cat incident-note.md | python3 scripts/ingest.py - --id INC-1234
```

命令行生成的案例默认是 `status: draft`，需要人工复核后再提升为 verified。

### 检索知识库

```bash
python3 scripts/query.py "HikariPool-1 - Connection is not available, request timed out"
```

从标准输入读取日志：

```bash
cat error.log | python3 scripts/query.py -
```

### 生成图谱 JSON

```bash
python3 scripts/graph.py
```

### 运行健康检查

```bash
python3 scripts/lint_okf.py
```

lint 会检查 frontmatter、必填字段、重复 signatures、断链、缺失 citations 和未被引用的 raw source。

## 知识格式

每个 case 是带 YAML frontmatter 的 Markdown 文件：

```yaml
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

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/ingest/preview` | 流式抽取单条记录，仅预览，不落库。 |
| `POST` | `/api/ingest/preview_batch` | 按一级标题拆分 Markdown，并以 NDJSON 流式返回批量抽取事件。 |
| `POST` | `/api/ingest/commit` | 确认写入一条已复核案例。 |
| `POST` | `/api/ingest/commit_batch` | 批量确认写入多条案例。 |
| `GET` | `/api/knowledge` | 获取 verified 知识列表。 |
| `GET` | `/api/knowledge/{case_file}` | 获取单条知识详情和原始记录。 |
| `PUT` | `/api/knowledge/{case_file}` | 更新单条知识并刷新索引。 |
| `DELETE` | `/api/knowledge/{case_file}` | 删除单条知识，保留 raw 原始记录。 |
| `POST` | `/api/query` | 按日志或报错信息检索知识。 |
| `GET` | `/api/graph` | 返回知识图谱节点和边。 |
| `GET` | `/api/kb/stats` | 返回案例数、草稿数、signature 数和更新时间。 |
| `GET` | `/api/examples/ingest` | 返回一条示例原始记录和示例结构化案例。 |

## 配置说明

入库管线默认读取仓库根目录的 `config.yaml`。也可以通过 `INGEST_CONFIG` 指定其他配置文件：

```bash
INGEST_CONFIG=/path/to/config.yaml uvicorn server.server:app --port 8000
```

当 `env: dev` 时，`scripts/ingest.py` 会设置 `NO_PROXY=127.0.0.1`。

不要提交真实 API key。公开仓库建议只提交 `config.example.yaml`，把 `config.yaml`、私有网关地址和密钥放在本地私有配置中。

## Agent 集成

`SKILL.md` 面向 coding agent 或运维 agent。可以将它安装或引用到 agent 环境中，并要求 agent 在回答日志、异常、错误码、接口超时、服务重启、内存、性能下降等问题前先查询本知识库。

推荐检索顺序：

1. 从用户输入中抽取错误原文、异常类名、错误码和组件名。
2. 优先在 `wiki/cases/` 中按 `signatures` 精确检索。
3. 读取命中案例，并以 `## 解决方案` 为主要回答依据。
4. 精确检索失败时，可接入 QMD 等本地 Markdown 语义检索层作为补充。
5. 仍无命中时，明确说明知识库暂无相关案例。

## 质量护栏

本项目默认采用保守模式，优先保证排查结论可信。

- 预览后写入：模型抽取结果不会直接修改知识库。
- 保留原始签名：`signatures` 是检索命门，不能被改写。
- 原始记录不可变：每个案例都应能追溯到 raw source。
- 不自动合并案例：跨案例概念和归纳需要显式复核。
- 不编造解决方案：无命中就返回无命中，不能根据相似经验伪造答案。

## 开发

本项目使用 FastAPI、静态前端和普通 Python 脚本，没有前端构建步骤。

启动开发服务：

```bash
uvicorn server.server:app --reload --port 8000
```

建议的基础检查：

```bash
python3 scripts/lint_okf.py
python3 scripts/query.py "some error text"
python3 scripts/graph.py > /tmp/wiserec-graph.json
```

## 贡献指南

欢迎贡献。适合本项目的改进包括：

- 更安全的写入、复核和回滚流程。
- 在不破坏无命中门控的前提下提升检索质量。
- 更完整的 wiki 健康检查。
- 更清晰、可审计的 Web UI。
- 文档、示例和测试用例。

提交变更前建议：

1. 运行 `python3 scripts/lint_okf.py`。
2. 确认 `uvicorn server.server:app --port 8000` 能正常启动。
3. 不提交密钥、私有日志、客户数据或未经脱敏的原始事故记录。
4. 保证新增知识都能追溯到 raw source。

## 安全与隐私

原始排查记录可能包含凭证、客户数据、主机名、IP、内部服务名或专有实现细节。公开仓库前请先脱敏。

使用 LLM 抽取时，原始记录会发送到 `config.yaml` 中配置的 OpenAI 兼容接口。请根据组织安全要求选择模型提供方、网关和脱敏流程。

## 许可证

本项目使用 MIT License 开源。你可以在遵守 MIT License 条款的前提下自由使用、复制、修改、合并、发布、分发、再许可和销售本项目副本。
