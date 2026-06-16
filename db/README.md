# 检索索引说明书

本目录是 log-wiki 的**检索索引层**。它把"模糊召回"从"全量读 Markdown + token 交集"升级为 **BM25 全文检索**,让知识文档逐步变多时检索依然又快又准。

## 1. 设计定位:索引是派生的,Markdown 才是权威源

- 知识的唯一权威源始终是 `wiki/cases/*.md`(OKF 不可变 / 可复核的 Markdown)。
- 本索引库**完全由 Markdown 文件回灌而来**,可随时整库重建,丢了也不影响知识本身。
- 入库 / 更新 / 删除知识时,后端([server/server.py](../server/server.py))会**自动同步**索引;服务启动时还会整库重建一次,保证与磁盘一致。
- `signatures` 的**精确命中**(子串匹配)仍在应用层做,优先级最高,是"无命中门控"的依据,**不**交给全文检索的相关度排序。

```
wiki/cases/*.md  ──(回灌/同步)──▶  index/search.db  ──(检索)──▶  query.search()
   权威源                              派生索引(可重建)
```

## 2. 当前实现:SQLite + FTS5(trigram)

- **本地文件库**:`index/search.db`(已 gitignore,不入库)。零网络、零费用,契合"检索不依赖外部网络"的护栏。
- **语法与 Cloudflare D1 一致**:后期要上 D1 几乎零改动。
- **中文检索**:用 FTS5 的 `trigram` 分词器(3 字滑窗),中英文混排都能子串匹配,无需额外分词插件。
  - ⚠️ trigram 下查询词需 **≥ 3 个字符**;2 字中文(如"内存")要靠更长的上下文片段命中。

建表 DDL:[db/schema.sqlite.sql](schema.sqlite.sql)。后端首次连接时会自动执行(`CREATE TABLE IF NOT EXISTS ...`),无需手工建库。

## 3. 表结构

| 表 | 作用 |
| --- | --- |
| `cases` | 一行一个案例(派生自一个 `wiki/cases/*.md`)。`rowid` 与 FTS 表对齐,`id` 是 slug(= 文件名主干)。 |
| `case_signatures` | 精确命中专用,一条 signature 一行;应用层判断"signature 是否作为子串出现在用户日志里"。 |
| `cases_fts` | FTS5 虚拟表,模糊召回用;`rowid` 与 `cases.rowid` 一一对应,`bm25()` 排序。 |

字段明细见 [schema.sqlite.sql](schema.sqlite.sql) 注释。

## 4. 如何从表中查询数据

### 4.1 整库重建 / 查看状态(命令行)

```bash
python scripts/search_index.py reindex        # 从 wiki/cases/ 整库重建索引
python scripts/search_index.py stats          # 查看案例数 / signature 数 / FTS 可用性
python scripts/search_index.py search "把整段报错粘进来"   # 走完整检索(精确→模糊→门控)
```

### 4.2 直接用 sqlite3 查(排障 / 验证用)

```bash
sqlite3 index/search.db
```

```sql
-- 看索引里有哪些案例
SELECT id, title, status, file FROM cases ORDER BY updated_at DESC;

-- 精确命中:某条 signature 是否会被某段日志命中(应用层逻辑的等价手查)
SELECT case_id, signature FROM case_signatures
WHERE instr('你的整段日志(小写)', lower(signature)) > 0;

-- 模糊召回:BM25 全文检索(bm25 越小越相关 → ORDER BY ASC)
SELECT c.id, c.title, c.file, bm25(cases_fts) AS score
FROM cases_fts JOIN cases c ON c.rowid = cases_fts.rowid
WHERE cases_fts MATCH '"连接池" OR "timed out" OR "HikariPool"'
ORDER BY score ASC
LIMIT 5;

-- 只在已复核案例里模糊召回(前置过滤,提精度)
SELECT c.id, c.title, bm25(cases_fts) AS score
FROM cases_fts JOIN cases c ON c.rowid = cases_fts.rowid
WHERE cases_fts MATCH '"OOM" OR "内存溢出"' AND c.status = 'verified'
ORDER BY score ASC;
```

> MATCH 查询要把任意文本拆成"加引号的短语"再用 `OR` 连接,直接喂整段日志会触发 FTS5 语法错误。
> 后端 `search_index._fts_query()` 已自动完成这一步(抽取 ≥3 字符的英文词 / 数字码 / 中文片段)。

### 4.3 代码里调用(推荐)

```python
import search_index
search_index.backend.search("把整段报错粘进来")
# -> {"mode": "exact"|"fuzzy"|"none", "hits": [...], "elapsed_ms": 3}
```

后端 `/api/query` 即走这条路;FTS5 不可用时 `query.py` 会自动回退到纯文件扫描,功能不变、只是慢一点。

## 5. 后期迁移

检索被封装在 `SearchBackend` 接口([scripts/search_index.py](../scripts/search_index.py))后面,换库只需新增一个实现类,`query.py` / `server.py` 调用面不变。

| 目标 | 改动 | 说明 |
| --- | --- | --- |
| **Cloudflare D1** | 几乎为零 | D1 就是 SQLite,FTS5 + trigram 同样支持;主要是把"本地文件连接"换成"D1 HTTP/Workers 绑定"。注意 D1 是远程库,会引入网络依赖。 |
| **MySQL** | 重写全文检索一层 | 业务表与 `case_signatures` 不变;`FTS5 + MATCH + bm25()` 换成 `FULLTEXT + MATCH ... AGAINST`,中文**必须**用 `WITH PARSER ngram`。DDL 见 [schema.mysql.sql](schema.mysql.sql)。 |

无论换哪个库,`signatures` 精确命中都继续留在应用层,不随后端改变。
