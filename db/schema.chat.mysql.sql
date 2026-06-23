-- =============================================================================
-- log-wiki 对话(Agent)持久化表结构（MySQL 版）
-- =============================================================================
-- 与 SQLite 版 db/schema.chat.sql 等价。用于 storage.backend=mysql 时保存
-- 会话、消息、反馈和时延指标。
-- =============================================================================

CREATE TABLE IF NOT EXISTS t_session_sources (
  code        VARCHAR(64) PRIMARY KEY COMMENT '来源编码, 如 web/api/cli',
  service     VARCHAR(64) NOT NULL COMMENT '来源服务, 如 wiserec-wiki/openapi/wechat',
  scene       VARCHAR(64) NOT NULL COMMENT '来源场景, 如 chat/embed/ops',
  description VARCHAR(255) COMMENT '来源说明',
  enabled     TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用: 1 启用, 0 停用',
  created_at  VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 创建时刻',
  updated_at  VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 更新时刻'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_chat_sessions (
  id          VARCHAR(64) PRIMARY KEY COMMENT '会话 id, uuid',
  user_id     VARCHAR(64) COMMENT '用户 id, 标识该会话归属的用户',
  source_code VARCHAR(64) NOT NULL DEFAULT 'web' COMMENT '会话来源编码, 关联 t_session_sources.code',
  title       VARCHAR(255) NOT NULL DEFAULT '新会话' COMMENT '会话标题, 默认取首条用户提问的前若干字',
  created_at  VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 会话创建时刻',
  updated_at  VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 最后一条消息时刻, 用于列表按活跃排序',
  INDEX idx_chat_sessions_updated (updated_at DESC),
  INDEX idx_chat_sessions_user (user_id),
  INDEX idx_chat_sessions_source (source_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_chat_messages (
  id               VARCHAR(64) PRIMARY KEY COMMENT '消息 id, uuid, 点赞点踩按此关联',
  session_id       VARCHAR(64) NOT NULL COMMENT '关联 t_chat_sessions.id',
  user_id          VARCHAR(64) COMMENT '用户 id, 标识该消息归属的用户',
  seq              INTEGER NOT NULL COMMENT '会话内自增序号, 保证消息严格有序',
  role             VARCHAR(32) NOT NULL COMMENT '消息角色, user 或 assistant',
  content          MEDIUMTEXT NOT NULL COMMENT '消息正文, 用户提问原文或 Agent 完整回复',
  answer_source    VARCHAR(32) COMMENT '仅 assistant: wiki 表示检索命中, llm 表示大模型兜底',
  retrieval_mode   VARCHAR(32) COMMENT '仅 assistant: 检索结论 exact/fuzzy/none',
  refs             MEDIUMTEXT COMMENT '仅 assistant: 来源 wiki 列表 JSON',
  elapsed_ms       INTEGER COMMENT '兼容旧字段: 历史上存检索耗时, 新数据存总耗时毫秒',
  retrieval_ms     INTEGER COMMENT '仅 assistant: 知识库检索耗时毫秒',
  model_wait_ms    INTEGER COMMENT '仅 assistant: 从请求模型到首字的等待耗时毫秒',
  first_delta_ms   INTEGER COMMENT '仅 assistant: 从后端开始处理到首个模型正文 token 的耗时毫秒',
  total_ms         INTEGER COMMENT '仅 assistant: 从后端开始处理到回复完成并落库的总耗时毫秒',
  message_count    INTEGER COMMENT '仅 assistant: 本轮发送给模型的 messages 数',
  prompt_chars     INTEGER COMMENT '仅 assistant: 本轮发送给模型的总字符数',
  history_messages INTEGER COMMENT '仅 assistant: 本轮注入的历史消息数',
  created_at       VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 消息创建时刻',
  INDEX idx_chat_messages_session (session_id, seq),
  INDEX idx_chat_messages_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_chat_feedbacks (
  id          VARCHAR(64) PRIMARY KEY COMMENT '反馈 id, uuid',
  message_id  VARCHAR(64) NOT NULL UNIQUE COMMENT '关联 t_chat_messages.id, 一条消息最多一条反馈',
  session_id  VARCHAR(64) NOT NULL COMMENT '冗余保存会话 id, 便于按会话聚合统计',
  user_id     VARCHAR(64) COMMENT '用户 id, 标识该反馈归属的用户',
  rating      VARCHAR(16) NOT NULL COMMENT '反馈类型, up 为点赞, down 为点踩',
  reason      TEXT COMMENT '点踩原因, 点赞时为空',
  created_at  VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 反馈创建时刻',
  updated_at  VARCHAR(40) NOT NULL COMMENT 'ISO 时间, 覆盖更新反馈时刷新',
  INDEX idx_chat_feedbacks_rating (rating),
  INDEX idx_chat_feedbacks_session (session_id),
  INDEX idx_chat_feedbacks_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
