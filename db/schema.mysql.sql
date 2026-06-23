-- =============================================================================
-- log-wiki 检索索引表结构（MySQL 版）
-- =============================================================================
-- 与 SQLite 版（db/schema.sqlite.sql）等价，差异只在「全文检索」一层：
--   - SQLite：FTS5 虚拟表 + trigram 分词器 + bm25()
--   - MySQL ：InnoDB FULLTEXT 索引 + MATCH ... AGAINST（TF-IDF 变体排序）
-- 业务字段、精确命中表 t_case_signatures 完全一致，应用层（SearchBackend 接口）只需换实现。
--
-- 关键点：中文检索必须用 ngram 解析器（WITH PARSER ngram），否则中文整段被当成一个词。
-- 适用：MySQL 5.7+ / 8.0（内置 ngram）。
-- =============================================================================

CREATE TABLE IF NOT EXISTS t_cases (
  id               VARCHAR(128) PRIMARY KEY COMMENT '案例 slug, 等于 wiki/cases/<id>.md 的文件名主干',
  file             VARCHAR(255) NOT NULL COMMENT '案例 Markdown 文件相对仓库根目录的路径',
  title            VARCHAR(512) COMMENT '案例标题',
  category         VARCHAR(128) COMMENT '案例类别, 如数据库/网络/训练卡住等',
  status           VARCHAR(32) COMMENT '案例状态, 如 verified 或 draft',
  confidence       VARCHAR(32) COMMENT '案例置信度, 如 high/medium/low',
  components       TEXT COMMENT '相关服务或组件列表, 以换行连接',
  signatures_text  TEXT COMMENT '错误特征 signatures 汇总文本, 以换行连接',
  background       MEDIUMTEXT COMMENT '问题背景或故障现象正文',
  diagnosis        MEDIUMTEXT COMMENT '定位过程正文',
  solution         MEDIUMTEXT COMMENT '解决方案正文',
  updated_at       VARCHAR(40) COMMENT 'ISO 时间, 案例文件的最后修改时间',
  -- 中文/混合内容全文索引：必须 ngram 解析器
  FULLTEXT INDEX ft_search (title, signatures_text, components, background, diagnosis, solution) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS t_case_signatures (
  case_id    VARCHAR(128) NOT NULL COMMENT '关联 t_cases.id 的案例 slug',
  signature  TEXT NOT NULL COMMENT '用于精确命中的原始错误特征文本',
  INDEX idx_case_signatures_case (case_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 模糊召回查询示例（替换 SQLite 的 MATCH t_cases_fts）：
--   SELECT id, title, file,
--          MATCH(title, signatures_text, components, background, diagnosis, solution)
--                AGAINST(? IN NATURAL LANGUAGE MODE) AS score
--   FROM t_cases
--   WHERE MATCH(title, signatures_text, components, background, diagnosis, solution)
--         AGAINST(? IN NATURAL LANGUAGE MODE)
--   ORDER BY score DESC
--   LIMIT 10;
