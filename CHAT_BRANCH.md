# chat 分支维护手册

本分支从 `main` 派生，只保留**后端对话能力 + MySQL 存储**。为了让 main 的后续改动能低成本合入，遵守两条纪律：

1. **保留的文件与 main 逐字一致**（除下方"接线文件"清单），不做文件内裁剪；
2. **不需要的模块整文件删除**，合并时遇到 modify/delete 冲突一律重新删除。

## 与 main 的差异

### 接线文件（本分支有意修改，冲突时保留 chat 侧接线、吸收 main 侧新逻辑）

- `src/llm_wiki/backend/server.py` — 只挂 chat 路由，去掉静态页与其它 router
- `src/llm_wiki/chat_store/__init__.py` — `_backend()` 固定 `MySQLChatStore`
- `src/llm_wiki/search_index/__init__.py` — `make_backend()` 固定 `MySQLSearch`
- `pyproject.toml` — 删除已移除模块的 script 入口
- `config.example.yaml` — mysql 必填、`local_search: false`
- `tests/conftest.py` — MySQL 测试库门控（无测试库时跳过落库用例）
- `.github/workflows/ci.yml` — MySQL service 容器 + chat 分支触发
- `README.md` / `CHAT_BRANCH.md` — 分支自有文档，冲突时保留 chat 侧（`git checkout --ours`）

### 已删除（合并出现 modify/delete 冲突时，重新 `git rm`）

```
frontend/ assets/ wiki/
src/llm_wiki/backend/api/ingest.py
src/llm_wiki/backend/api/knowledge.py
src/llm_wiki/backend/api/search.py
src/llm_wiki/backend/api/eval.py
src/llm_wiki/backend/api/graph.py
src/llm_wiki/backend/api/static_pages.py
src/llm_wiki/backend/search_sync.py
src/llm_wiki/knowledge/ingest.py
src/llm_wiki/knowledge/graph.py
src/llm_wiki/knowledge/lint_okf.py
src/llm_wiki/search_index/sqlite_backend.py
src/llm_wiki/search_index/eval.py
src/llm_wiki/chat_store/sqlite_store.py
db/schema.chat.sql
db/schema.sqlite.sql
tests/test_chat_store_sqlite.py
tests/test_search_index.py
tests/test_retrieval_eval.py
```

## main → chat 合并流程

```bash
git checkout chat
git merge main
# 1) 已删文件的 modify/delete 冲突:重新删除(把冲突路径替换进去)
git rm -r <冲突路径...>
# 2) 接线文件冲突:人工合并,保留 chat 侧接线
# 3) README/CHAT_BRANCH.md 冲突:git checkout --ours README.md
git merge --continue
ruff check . && pytest -q
```

建议 main 每合入几个 PR 就同步一次，小步合并、避免积压。

合并后自查：

```bash
# 不应有任何输出(main 新增代码若引用了被删模块,需要在 chat 侧补删或改接线)
grep -rn "sqlite_backend\|sqlite_store\|search_sync\|static_pages" src tests --include='*.py' | grep -v Binary
python -c "import sys; sys.path.insert(0, 'src'); import llm_wiki.backend.server"
```
