# LLM Wiki — chat 分支（对话后端）

本分支是 LLM Wiki 的**纯后端对话服务**：只保留对话 Agent 的 HTTP API（RAG + LLM 兜底 + 流式输出），存储固定使用 **MySQL**，不包含前端页面、知识写入/编辑、图谱等功能。完整功能请看 `main` 分支；本分支的取舍原则与合并流程见 [CHAT_BRANCH.md](CHAT_BRANCH.md)。

## 对话逻辑

每轮提问的处理流程（与 main 一致，未做任何简化）：

1. 用 MySQL 检索索引（`t_cases` / `t_case_signatures`）检索用户输入：signature 精确命中（Aho-Corasick），或 FULLTEXT 模糊召回且关联度达阈值。
2. **命中 → RAG**：把案例资料（背景/定位/解决方案)注入上下文，由大模型基于资料流式作答，标注来源 wiki。
3. **未命中 → LLM 兜底**：不带知识库资料，直接由大模型流式作答。
4. 会话、消息、反馈与时延指标全部落 MySQL（`t_chat_sessions` / `t_chat_messages` / `t_chat_feedbacks`）。

知识数据由 main 分支（或其它运营入口）写入 MySQL；本分支只读检索表，不读本地 Markdown 文件（`local_search: false`）。

## 快速开始

环境要求：Python 3.9+、可访问的 MySQL、OpenAI 兼容 API key。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # 开发再装 pip install -e ".[dev]"
cp config.example.yaml config.yaml   # 填 chat 段密钥与 storage.mysql 连接信息
uvicorn --app-dir src llm_wiki.backend.server:app --port 8000
```

首次访问会自动在 MySQL 中建表。配置说明见 `config.example.yaml` 内注释；`chat` 段以 `openai` 段为基底，只需覆盖要改的项。

注意：本分支没有 `wiki/cases/` 文件源，`storage.auto_reindex_on_startup` 必须保持 `false`，否则启动会清空检索表。

## API 一览

均为 POST；对话消息接口返回 NDJSON 流（`status` / `meta` / `delta` / `done` / `error` 事件）。

| 接口 | 说明 |
| --- | --- |
| `/api/chat/sessions` | 创建会话 |
| `/api/chat/sessions/list` | 会话列表（可按 `user_id` 隔离） |
| `/api/chat/sessions/clear` | 清空会话 |
| `/api/chat/sessions/{id}/messages` | 发送消息，流式返回回答 |
| `/api/chat/sessions/{id}/messages/list` | 读会话消息 |
| `/api/chat/sessions/{id}/delete` | 删除会话 |
| `/api/chat/messages/{id}/feedback` | 点赞/点踩/取消 |

## 开发与测试

```bash
ruff check .
pytest -q
```

落库用例需要专用 MySQL 测试库，通过环境变量提供（详见 `tests/conftest.py`；不设置则自动跳过，CI 由 service 容器提供）：

```bash
export LOG_WIKI_MYSQL_HOST=127.0.0.1 LOG_WIKI_MYSQL_PORT=3306 \
       LOG_WIKI_MYSQL_USER=root LOG_WIKI_MYSQL_PASSWORD=root \
       LOG_WIKI_MYSQL_DATABASE=llm_wiki_test
```

排障 CLI：`python -m llm_wiki.chat_store stats`、`python -m llm_wiki.search_index stats|search`。

## 许可证

MIT，见 [LICENSE](LICENSE)。
