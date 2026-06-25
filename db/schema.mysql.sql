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

-- 表/列 collation 显式钉死 utf8mb4_general_ci（5.7 与 8.0 都内置，避免依赖 server 默认：
-- 8.0 默认 utf8mb4_0900_ai_ci 是「重音不敏感」，会让精确命中行为跨版本漂移）。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 精确命中专用表。要点：
--   1. 显式主键 id（CHAR(64) UUID，应用层 uuid4 生成）—— InnoDB 是索引组织表，没有显式主键
--      会生成业务不可用的 6 字节隐藏聚簇键；用 UUID 而非自增，便于多源写入/迁移时主键不冲突。
--   2. 外键 case_id -> t_cases.id ON DELETE CASCADE —— 删案例时自动清理 signature，杜绝孤儿行
--      污染「无命中门控」的最高优先级精确命中（应用层不再需要单独 DELETE，但保留亦无害）。
--   3. UNIQUE(case_id, signature(255)) —— 同一案例同一 signature 不重复入库，避免同一命中被重复计数；
--      兼作 case_id 的索引（外键所需），故不再单列 idx_case_signatures_case。
--   4. signature 列固定 utf8mb4_bin —— 精确命中要求「原文照搬」的二进制比较；配合应用层两侧 LOWER()，
--      得到与 SQLite instr(lower()) 完全一致、且跨 MySQL 版本稳定的「忽略大小写」子串匹配。
CREATE TABLE IF NOT EXISTS t_case_signatures (
  id         CHAR(64) NOT NULL PRIMARY KEY COMMENT '行主键, 应用层 uuid4 生成',
  case_id    VARCHAR(128) NOT NULL COMMENT '关联 t_cases.id 的案例 slug',
  signature  TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '用于精确命中的原始错误特征文本',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间, 由 DB 默认值填充, 调试/审计用',
  UNIQUE KEY uniq_case_signature (case_id, signature(255)),
  CONSTRAINT fk_case_signatures_case FOREIGN KEY (case_id) REFERENCES t_cases(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 模糊召回查询示例（替换 SQLite 的 MATCH t_cases_fts）：
--   SELECT id, title, file,
--          MATCH(title, signatures_text, components, background, diagnosis, solution)
--                AGAINST(? IN NATURAL LANGUAGE MODE) AS score
--   FROM t_cases
--   WHERE MATCH(title, signatures_text, components, background, diagnosis, solution)
--         AGAINST(? IN NATURAL LANGUAGE MODE)
--   ORDER BY score DESC
--   LIMIT 10;
