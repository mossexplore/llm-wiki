"""测试全局配置(chat 分支):对话存储只有 MySQL 后端。

- 把 INGEST_CONFIG 指向空设备,强制测试忽略仓库根目录的 config.yaml,
  避免用例误连真实业务库;测试库只能用环境变量显式指定。
- 设置 LOG_WIKI_MYSQL_HOST/PORT/USER/PASSWORD/DATABASE 后,落库用例才会运行,
  且会清空该库的会话数据 —— 务必指向专用测试库,不要指向生产库;
  未设置或连接失败时,落库用例整体跳过,纯函数用例照常运行(CI 由 service 容器提供测试库)。
"""

from __future__ import annotations

import os

import pytest

# 必须在任何 llm_wiki 模块导入之前设置:paths.CONFIG_PATH 在导入时读取。
os.environ["INGEST_CONFIG"] = os.devnull

# 依赖真实 MySQL 的测试模块;其余用例均为纯函数或 monkeypatch,不落库。
_DB_TEST_FILES = {"test_chat_sessions_api.py", "test_chat_api_message_format.py"}

_REQUIRED_ENV = ("LOG_WIKI_MYSQL_USER", "LOG_WIKI_MYSQL_PASSWORD", "LOG_WIKI_MYSQL_DATABASE")


def _mysql_skip_reason() -> str | None:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        return "需要 MySQL 测试库(未设置 " + ", ".join(missing) + ")"
    try:
        from llm_wiki import chat_store

        chat_store.stats()  # 探测连接并顺带建表
    except Exception as exc:
        return f"MySQL 测试库不可用: {exc.__class__.__name__}: {exc}"
    return None


def pytest_collection_modifyitems(config, items):
    reason = _mysql_skip_reason()
    if reason is None:
        return
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        if item.path.name in _DB_TEST_FILES:
            item.add_marker(marker)
