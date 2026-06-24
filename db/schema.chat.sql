-- =============================================================================
-- log-wiki 对话(Agent)持久化表结构（SQLite / Cloudflare D1）
-- =============================================================================
-- 角色定位：这是「运营数据层」，和检索索引(schema.sqlite.sql)不同 ——
--   - 检索索引是「派生」的，丢了可从 wiki/cases/*.md 重建；
--   - 本库是「权威」的运营数据：所有会话、用户提问、Agent 回复、点赞点踩都只存在这里，
--     用于后续的对话质量分析、知识盲区发现(点踩原因)、答案来源统计(wiki vs 大模型)。
--
-- 运行库默认在 db/chat.db（被 .gitignore 的 *.db 规则忽略，不入库，避免泄露对话内容）。
-- 后端 llm_wiki.chat_store 首次连接时自动执行本 DDL（CREATE TABLE IF NOT EXISTS ...）。
--
-- 适用：SQLite 3.x。使用 MySQL 后端时见 db/schema.chat.mysql.sql。
-- =============================================================================

-- ---------------------------------------------------------------------------
-- t_session_sources：会话来源字典表。
-- code 是业务侧传入的来源编码；service 表示来源服务；scene 表示来源场景。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_session_sources (
  code        TEXT PRIMARY KEY,          -- 来源编码，如 web / api / cli
  service     TEXT NOT NULL,             -- 来源服务，如 wiserec-wiki / openapi / wechat
  scene       TEXT NOT NULL,             -- 来源场景，如 chat / embed / ops
  description TEXT,                      -- 来源说明
  enabled     INTEGER NOT NULL DEFAULT 1, -- 是否启用：1 启用，0 停用
  created_at  TEXT NOT NULL,             -- ISO 时间，创建时刻
  updated_at  TEXT NOT NULL              -- ISO 时间，更新时刻
);
INSERT OR IGNORE INTO t_session_sources
  (code, service, scene, description, enabled, created_at, updated_at)
VALUES
  ('web', 'wiserec-wiki', 'chat', 'Web 页面聊天入口', 1,
   strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

-- ---------------------------------------------------------------------------
-- t_chat_sessions：一行一个会话（左侧「新建聊天」对应一行）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_chat_sessions (
  id          TEXT PRIMARY KEY,          -- 会话 id（uuid）
  user_id     TEXT,                      -- 用户 id，标识该会话归属的用户
  source_code TEXT NOT NULL DEFAULT 'web', -- 会话来源编码，关联 t_session_sources.code
  title       TEXT NOT NULL DEFAULT '新会话',  -- 会话标题，默认取首条用户提问的前若干字
  created_at  TEXT NOT NULL,             -- ISO 时间，创建时刻
  updated_at  TEXT NOT NULL             -- ISO 时间，最后一条消息时刻，用于列表按活跃排序
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON t_chat_sessions(updated_at DESC);

-- ---------------------------------------------------------------------------
-- t_chat_messages：一行一条消息（用户提问 or Agent 回复）。
-- 同一会话内按 created_at（+ seq）顺序排列，构成完整对话历史。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_chat_messages (
  id              TEXT PRIMARY KEY,      -- 消息 id（uuid），点赞点踩按此关联
  session_id      TEXT NOT NULL,         -- 关联 t_chat_sessions.id
  user_id         TEXT,                  -- 用户 id，标识该消息归属的用户
  seq             INTEGER NOT NULL,      -- 会话内自增序号，保证严格有序（同一毫秒也不乱）
  role            TEXT NOT NULL,         -- 'user' | 'assistant'
  content         TEXT NOT NULL,         -- 消息正文（用户提问原文 / Agent 完整回复）
  answer_source   TEXT,                  -- 仅 assistant：'wiki'(检索命中) | 'llm'(大模型兜底)
  retrieval_mode  TEXT,                  -- 仅 assistant：检索结论 'exact' | 'fuzzy' | 'none'
  refs            TEXT,                  -- 仅 assistant：来源 wiki 列表 JSON，如 [{"file":"...","title":"..."}]
  elapsed_ms      INTEGER,               -- 兼容旧字段：历史上存检索耗时，新数据存总耗时（毫秒）
  retrieval_ms    INTEGER,               -- 仅 assistant：知识库检索耗时（毫秒）
  model_wait_ms   INTEGER,               -- 仅 assistant：从请求模型到首字的等待耗时（毫秒）
  first_delta_ms  INTEGER,               -- 仅 assistant：从后端开始处理到首个模型正文 token 的耗时（毫秒）
  total_ms        INTEGER,               -- 仅 assistant：从后端开始处理到回复完成并落库的总耗时（毫秒）
  message_count   INTEGER,               -- 仅 assistant：本轮发送给模型的 messages 数
  prompt_chars    INTEGER,               -- 仅 assistant：本轮发送给模型的总字符数
  created_at      TEXT NOT NULL          -- ISO 时间
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON t_chat_messages(session_id, seq);

-- ---------------------------------------------------------------------------
-- t_chat_feedbacks：一行一条反馈（仅针对 assistant 消息）。
-- 一条消息最多保留一条反馈（同一消息再次反馈 = 覆盖更新）。点踩必须带原因，
-- 这些原因是发现「知识库盲区 / 答案不靠谱」的最直接运营信号。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_chat_feedbacks (
  id          TEXT PRIMARY KEY,          -- 反馈 id（uuid）
  message_id  TEXT NOT NULL UNIQUE,      -- 关联 t_chat_messages.id，一条消息一条反馈
  session_id  TEXT NOT NULL,             -- 冗余存一份，便于按会话聚合统计
  user_id     TEXT,                      -- 用户 id，标识该反馈归属的用户
  feedback    TEXT NOT NULL,             -- 'like'(点赞) | 'dislike'(点踩)
  reason      TEXT,                      -- 点踩原因:可为纯文本或结构化 JSON；点赞时为空
  created_at  TEXT NOT NULL,             -- ISO 时间
  updated_at  TEXT NOT NULL              -- ISO 时间，覆盖更新时刷新
);
CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_feedback ON t_chat_feedbacks(feedback);
CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_session ON t_chat_feedbacks(session_id);
