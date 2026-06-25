#!/usr/bin/env python3
"""
llm.py — LLM 接入层:config.yaml 配置段加载 + OpenAI 客户端构建。

这是对话与入库共用的底座,只负责「怎么连大模型」,不掺任何 wiki/检索/入库逻辑:
  - load_config(section)      读取并合并某个配置段(openai 为基底,其它段覆盖非空字段)。
  - client_and_model(section) 懒加载并缓存 OpenAI 客户端 + model 名。

差异化配置:写入知识(抽取)默认用 `openai` 段,对话用 `chat` 段。非 openai 段以 `openai`
为基底,再用本段的非空字段覆盖 —— 只需在 `chat` 里写要改的项(如只换 model),其余自动继承。
"""

from __future__ import annotations

import logging
import os
import threading

import yaml
from openai import OpenAI

from llm_wiki.common.paths import CONFIG_PATH

logger = logging.getLogger("log_wiki.llm")

_clients: dict = {}  # section -> (OpenAI client, model, config_key);按配置段缓存,配置变化时自动刷新
_clients_lock = threading.Lock()  # 批量预览用线程池并发取 client,_clients 读写需加锁

_config_cache = None  # (mtime, data):按文件 mtime 缓存解析结果,避免每次请求都读盘+解析 YAML
_config_lock = threading.Lock()


def _load_config_data() -> dict:
    """读取并缓存 config.yaml 的解析结果;按文件 mtime 失效,改配置无需重启即可生效。

    每次请求都重读+解析 YAML 是无谓开销;这里只在文件变更时重载一次。重载发生在锁内,
    并把「dev 环境设 NO_PROXY」这类一次性副作用收敛到此处,避免在每次配置读取时反复改全局环境。
    """
    global _config_cache
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"缺少配置文件 {CONFIG_PATH};请复制 config.example.yaml 为 config.yaml 并填写。")
    mtime = CONFIG_PATH.stat().st_mtime
    with _config_lock:
        if _config_cache is None or _config_cache[0] != mtime:
            data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            env = data.get("env", "dev")
            if env == "dev":
                os.environ["NO_PROXY"] = "127.0.0.1"
            logger.info(
                "llm.config.loaded env=%s config_path=%s no_proxy=%s",
                env,
                CONFIG_PATH,
                os.environ.get("NO_PROXY", ""),
            )
            _config_cache = (mtime, data)
        return _config_cache[1]


def load_config(section: str = "openai") -> dict:
    """读取本地 config.yaml 的某个配置段:api_key / base_url / model。

    差异化配置:写入知识(抽取)默认用 `openai` 段,对话用 `chat` 段。非 openai 段以
    `openai` 为基底,再用本段的非空字段覆盖 —— 这样只需在 `chat` 里写要改的项
    (如只换 model),其余自动继承 openai;`chat` 段缺省时则完全等同 openai(向后兼容)。

    缺失时抛 RuntimeError(普通异常,便于 Web 端 except Exception 捕获并流式回传);
    CLI 侧在 main() 里把它转成友好退出。
    """
    data = _load_config_data()
    cfg = dict(data.get("openai") or {})
    if section != "openai":
        override = data.get(section) or {}
        cfg.update({k: v for k, v in override.items() if v not in (None, "")})
    if not cfg.get("api_key"):
        raise RuntimeError(f"配置文件 {CONFIG_PATH} 缺少 {section}.api_key(或 openai.api_key)。")
    return cfg


def client_and_model(section: str = "openai"):
    """懒加载 OpenAI 客户端;按配置段缓存,配置变化时自动刷新。线程安全。"""
    cfg = load_config(section)
    model = cfg.get("model", "gpt-4o")
    config_key = (cfg.get("api_key"), cfg.get("base_url") or None, model)
    with _clients_lock:
        cached = _clients.get(section)
        if not cached or cached[2] != config_key:
            client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url") or None)
            _clients[section] = (client, model, config_key)
        client, model, _ = _clients[section]
    return client, model
