"""测试全局配置:把 chat store 指向临时 SQLite 库,避免污染 db/chat.db。

CHAT_DB 在 llm_wiki.chat_store.common 导入时读取,所以必须在任何 store 模块被
导入之前设置 —— conftest 在收集用例前最先导入,正好满足。
"""
import os
import pathlib
import tempfile

_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "llm_wiki_test_chat.db"
os.environ["CHAT_DB"] = str(_TEST_DB)
# 起始清掉残留,保证每次会话从空库开始
for suffix in ("", "-wal", "-shm"):
    p = pathlib.Path(str(_TEST_DB) + suffix)
    if p.exists():
        p.unlink()
