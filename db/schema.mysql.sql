-- =============================================================================
-- log-wiki 检索索引表结构（MySQL 版 · 后期切换用）
-- =============================================================================
-- 与 SQLite/D1 版（db/schema.sqlite.sql）等价，差异只在「全文检索」一层：
--   - SQLite：FTS5 虚拟表 + trigram 分词器 + bm25()
--   - MySQL ：InnoDB FULLTEXT 索引 + MATCH ... AGAINST（TF-IDF 变体排序）
-- 业务字段、精确命中表 case_signatures 完全一致，应用层（SearchBackend 接口）只需换实现。
--
-- 关键点：中文检索必须用 ngram 解析器（WITH PARSER ngram），否则中文整段被当成一个词。
-- 适用：MySQL 5.7+ / 8.0（内置 ngram）。
-- =============================================================================

CREATE TABLE IF NOT EXISTS cases (
  id               VARCHAR(128) PRIMARY KEY,   -- slug，= wiki/cases/<id>.md 文件名主干
  file             VARCHAR(255) NOT NULL,
  title            VARCHAR(512),
  category         VARCHAR(128),
  status           VARCHAR(32),                -- verified / draft
  confidence       VARCHAR(32),
  components       TEXT,
  signatures_text  TEXT,
  background       MEDIUMTEXT,
  diagnosis        MEDIUMTEXT,
  solution         MEDIUMTEXT,
  updated_at       DATETIME,
  -- 中文/混合内容全文索引：必须 ngram 解析器
  FULLTEXT INDEX ft_search (title, signatures_text, components, background, diagnosis, solution) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS case_signatures (
  case_id    VARCHAR(128) NOT NULL,
  signature  TEXT NOT NULL,
  INDEX idx_case_signatures_case (case_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 模糊召回查询示例（替换 SQLite 的 MATCH cases_fts）：
--   SELECT id, title, file,
--          MATCH(title, signatures_text, components, background, diagnosis, solution)
--                AGAINST(? IN NATURAL LANGUAGE MODE) AS score
--   FROM cases
--   WHERE MATCH(title, signatures_text, components, background, diagnosis, solution)
--         AGAINST(? IN NATURAL LANGUAGE MODE)
--   ORDER BY score DESC
--   LIMIT 10;
