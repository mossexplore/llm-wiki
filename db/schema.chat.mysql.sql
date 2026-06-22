-- =============================================================================
-- log-wiki 对话(Agent)持久化表结构（MySQL 版）
-- =============================================================================
-- 与 SQLite 版 db/schema.chat.sql 等价。用于 storage.backend=mysql 时保存
-- 会话、消息、反馈和时延指标。
-- =============================================================================

CREATE TABLE IF NOT EXISTS chat_sessions (
  id          VARCHAR(64) PRIMARY KEY,
  title       VARCHAR(255) NOT NULL DEFAULT '新会话',
  created_at  VARCHAR(40) NOT NULL,
  updated_at  VARCHAR(40) NOT NULL,
  INDEX idx_chat_sessions_updated (updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_messages (
  id               VARCHAR(64) PRIMARY KEY,
  session_id       VARCHAR(64) NOT NULL,
  seq              INTEGER NOT NULL,
  role             VARCHAR(32) NOT NULL,
  content          MEDIUMTEXT NOT NULL,
  answer_source    VARCHAR(32),
  retrieval_mode   VARCHAR(32),
  refs             MEDIUMTEXT,
  elapsed_ms       INTEGER,
  retrieval_ms     INTEGER,
  model_wait_ms    INTEGER,
  first_delta_ms   INTEGER,
  total_ms         INTEGER,
  message_count    INTEGER,
  prompt_chars     INTEGER,
  history_messages INTEGER,
  created_at       VARCHAR(40) NOT NULL,
  INDEX idx_chat_messages_session (session_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_feedback (
  id          VARCHAR(64) PRIMARY KEY,
  message_id  VARCHAR(64) NOT NULL UNIQUE,
  session_id  VARCHAR(64) NOT NULL,
  rating      VARCHAR(16) NOT NULL,
  reason      TEXT,
  created_at  VARCHAR(40) NOT NULL,
  updated_at  VARCHAR(40) NOT NULL,
  INDEX idx_chat_feedback_rating (rating),
  INDEX idx_chat_feedback_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
