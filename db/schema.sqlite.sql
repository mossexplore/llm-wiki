-- =============================================================================
-- log-wiki 检索索引表结构（SQLite / Cloudflare D1）
-- =============================================================================
-- 角色定位：本库是「派生索引」，不是真理之源。
--   - 知识的权威源始终是 wiki/cases/*.md（OKF 不可变 / 可复核的 Markdown）。
--   - 本库由 Markdown 文件回灌而来，用于「快速模糊检索」，可随时整库重建。
--   - 入库 / 更新 / 删除知识时，由后端同步维护本库（见 scripts/search_index.py）。
--
-- 为什么用 SQLite：本地零依赖、零网络、毫秒级，契合「检索不依赖外部网络」的护栏；
-- 语法与 Cloudflare D1 一致，后期可平滑迁 D1；迁 MySQL 见 db/schema.mysql.sql。
--
-- 适用：SQLite 3.34+（trigram 分词器）。检索全文检索能力需要 FTS5 编译选项（默认开启）。
-- =============================================================================

-- ---------------------------------------------------------------------------
-- cases：一行一个案例（派生自一个 wiki/cases/*.md 文件）
-- 主键 rowid 是自增整数，用于和 FTS 虚拟表对齐（FTS5 以 rowid 关联）。
-- 业务键 id = 案例文件名去掉 .md 的 slug（与后端写文件、删文件用的 key 一致）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cases (
  rowid            INTEGER PRIMARY KEY AUTOINCREMENT,
  id               TEXT    NOT NULL UNIQUE,   -- slug，= wiki/cases/<id>.md 的文件名主干
  file             TEXT    NOT NULL,          -- 相对仓库根的路径，如 wiki/cases/xxx.md
  title            TEXT,
  category         TEXT,
  status           TEXT,                      -- verified / draft
  confidence       TEXT,                      -- high / medium / low
  components       TEXT,                      -- 组件列表，以换行连接，仅供展示/调试
  signatures_text  TEXT,                      -- signatures 列表，以换行连接（精确命中走 case_signatures）
  background       TEXT,                      -- 「问题背景」正文
  diagnosis        TEXT,                      -- 「定位过程」正文
  solution         TEXT,                      -- 「解决方案」正文（精确命中直接回这段，省去读文件）
  updated_at       TEXT                       -- ISO 时间，案例文件的最后修改时间
);

-- ---------------------------------------------------------------------------
-- case_signatures：精确命中专用，一条 signature 一行。
-- 检索时把全部 signature 拉到应用层，判断「某条 signature 是否作为子串出现在用户日志里」。
-- 这是知识库的「检索命门」：signature 必须原文照搬，精确命中优先级最高、且是无命中门控的依据。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS case_signatures (
  case_id    TEXT NOT NULL,                   -- 关联 cases.id
  signature  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_signatures_case ON case_signatures(case_id);

-- ---------------------------------------------------------------------------
-- cases_fts：全文检索（模糊召回），FTS5 + trigram 分词器。
-- trigram 按 3 字滑窗切词，中英文混排都能做子串匹配（中文无需额外分词插件）。
-- rowid 与 cases.rowid 一一对应；排序用内置 bm25()（越小越相关，ORDER BY ... ASC）。
-- 注意：trigram 下「查询词」需 >= 3 个字符，2 字中文（如「内存」）需靠更长的上下文片段命中。
-- ---------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
  title,
  signatures_text,
  components,
  body,                                       -- background + diagnosis + solution 拼接
  tokenize = 'trigram'
);
