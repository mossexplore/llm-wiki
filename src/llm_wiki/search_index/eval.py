#!/usr/bin/env python3
"""检索质量评测:固定语料 + 标注查询 + recall@k / MRR。

这是检索优化(精确命中、模糊召回、后续重排/向量)前后的对比基线,既被 pytest
当回归门槛,也被 Web「评测」页与 CLI 调用。

数据放在包内(而非 tests/)是为了让运行中的后端进程也能加载并评测。

运行:
    python -m llm_wiki.search_index.eval               # 评隔离沙箱 SQLite(FTS5)
    EVAL_BACKEND=mysql python -m llm_wiki.search_index.eval   # 评 MySQL FULLTEXT(需配 storage.mysql)

设计要点:
  - 自带固定语料(CORPUS)+ 标注查询(QUERIES),不依赖 wiki/cases/,可复现。
  - 查询按 kind 分组,刻意包含「换种说法/近义词」的语义类查询 —— 当前纯词法检索
    本就召不回它们,基线如实暴露这个缺口,正是后续上重排/向量要补的部分。
  - Web 运行走 run_eval():把语料索引进临时 SQLite 沙箱,绝不触碰生产检索索引。
"""

from __future__ import annotations

import pathlib
import tempfile
import time

K = 3  # 与对话注入 / 检索默认 limit 对齐


def _case(cid, title, category, signatures, components, background, diagnosis, solution):
    return {
        "id": cid,
        "file": f"wiki/cases/{cid}.md",
        "title": title,
        "category": category,
        "status": "verified",
        "confidence": "high",
        "signatures": signatures,
        "components": components,
        "background": background,
        "diagnosis": diagnosis,
        "solution": solution,
        "updated_at": "2026-01-01T00:00:00",
    }


CORPUS = [
    _case(
        "hikari-pool-timeout",
        "HikariPool 连接池耗尽致接口批量 500",
        "数据库 / 连接池",
        ["HikariPool-1 - Connection is not available, request timed out"],
        ["order-service", "HikariCP", "MySQL"],
        "大促高峰期订单接口批量返回 500,持续约十分钟。",
        "慢查询长时间占满连接,连接池无空闲连接可分配,新请求等待超时。",
        "为慢查询加复合索引,并把 maximumPoolSize 从 10 调到 30,超时告警接入。",
    ),
    _case(
        "container-oom-killed",
        "容器内存超限被 OOMKilled 反复重启",
        "内存",
        ["Out of memory: Killed process", "java.lang.OutOfMemoryError: Java heap space"],
        ["payment-service", "JVM", "Kubernetes"],
        "支付服务 Pod 反复重启,事件里是 OOMKilled。",
        "堆内对象长期不释放,容器内存达到 limit 被内核 oom-killer 杀死。",
        "修复缓存未设上限的内存泄漏,调大 limit 并设置 -Xmx 留出堆外余量。",
    ),
    _case(
        "nginx-upstream-502",
        "Nginx 上游过早关闭连接返回 502",
        "网络 / 网关",
        ["upstream prematurely closed connection while reading response header", "502 Bad Gateway"],
        ["nginx", "gateway", "tomcat"],
        "网关偶发 502,后端服务自身日志却看不到异常。",
        "后端 keepalive 超时短于网关,连接被后端先关,网关读响应头时连接已断。",
        "把后端 keepalive_timeout 调大于网关,统一连接复用参数。",
    ),
    _case(
        "redis-read-timeout",
        "Redis 读超时导致缓存击穿",
        "缓存",
        ["redis.clients.jedis.exceptions.JedisConnectionException", "Read timed out"],
        ["user-service", "Redis", "Jedis"],
        "用户信息接口偶发变慢,日志里有 Redis 读超时。",
        "大 key 一次性读取阻塞,叠加网络抖动触发读超时,请求穿透到数据库。",
        "拆分大 key,设置合理的 so_timeout 与连接池,热点数据加本地缓存。",
    ),
    _case(
        "kafka-consumer-rebalance",
        "Kafka 消费组反复 rebalance 消费停滞",
        "消息队列",
        ["Attempt to heartbeat failed since group is rebalancing", "CommitFailedException"],
        ["risk-service", "Kafka"],
        "风控消费组消费速率周期性掉零,日志刷 rebalancing。",
        "单条处理耗时超过 max.poll.interval.ms,消费者被踢出触发再均衡循环。",
        "缩小 max.poll.records,拉长 max.poll.interval.ms,重活异步化。",
    ),
    _case(
        "disk-no-space",
        "磁盘写满导致服务无法写日志与落库",
        "存储",
        ["No space left on device", "ENOSPC"],
        ["log-agent", "ext4"],
        "多台机器同时告警,应用报无法写文件。",
        "日志未轮转,单分区被历史日志占满,写操作全部失败。",
        "清理并配置 logrotate,磁盘使用率接入容量告警。",
    ),
    _case(
        "ssl-cert-expired",
        "证书过期导致 HTTPS 调用握手失败",
        "安全 / 证书",
        ["PKIX path validation failed", "certificate expired"],
        ["api-gateway", "OpenSSL"],
        "对外回调突然全部失败,客户端报证书校验失败。",
        "中间 CA 证书过期未及时续期,TLS 握手在证书链校验阶段中断。",
        "续期并自动化证书轮转,到期前 30 天接入提醒。",
    ),
    _case(
        "jvm-long-gc-pause",
        "JVM 长时间 GC 停顿引发请求超时",
        "性能 / GC",
        ["Total time for which application threads were stopped"],
        ["search-service", "JVM", "G1GC"],
        "搜索服务 P99 周期性飙高,客户端大量超时。",
        "老年代回收触发长 STW,应用线程停顿数秒,期间请求全部排队超时。",
        "切 G1 并调小 region 与暂停目标,降低单次回收对象量。",
    ),
]

CASE_IDS = {c["id"] for c in CORPUS}


def _q(query, expected, kind):
    assert expected in CASE_IDS, expected
    return {"query": query, "expected": expected, "kind": kind}


# kind:
#   exact      —— 原文粘贴 signature,应稳定走精确命中
#   lexical    —— 中文换种说法,但与案例正文有词面重合,词法检索有机会
#   semantic   —— 近义/同义但词面几乎不重合,纯词法预期召不回(暴露语义缺口)
QUERIES = [
    _q(
        "线上报错 HikariPool-1 - Connection is not available, request timed out after 30007ms",
        "hikari-pool-timeout", "exact",
    ),
    _q("Out of memory: Killed process 2731 (java)", "container-oom-killed", "exact"),
    _q(
        "nginx 日志:upstream prematurely closed connection while reading response header",
        "nginx-upstream-502", "exact",
    ),
    _q(
        "caused by redis.clients.jedis.exceptions.JedisConnectionException: Read timed out",
        "redis-read-timeout", "exact",
    ),
    _q("WARN Attempt to heartbeat failed since group is rebalancing", "kafka-consumer-rebalance", "exact"),
    _q("write failed: No space left on device (ENOSPC)", "disk-no-space", "exact"),
    _q("javax.net.ssl: PKIX path validation failed certificate expired", "ssl-cert-expired", "exact"),
    _q(
        "safepoint: Total time for which application threads were stopped: 4.83s",
        "jvm-long-gc-pause", "exact",
    ),

    _q("数据库连接池被慢查询占满,订单接口大量超时报错", "hikari-pool-timeout", "lexical"),
    _q("支付服务容器内存超限反复重启 OOMKilled", "container-oom-killed", "lexical"),
    _q("Kafka 消费组一直 rebalance,消费速率掉零", "kafka-consumer-rebalance", "lexical"),
    _q("磁盘分区写满,应用无法写日志和落库", "disk-no-space", "lexical"),

    _q("接口偶发性整批失败返回 500,是不是连接不够用了", "hikari-pool-timeout", "semantic"),
    _q("服务进程被系统杀掉,疑似吃内存太多", "container-oom-killed", "semantic"),
    _q("网关时不时报错,后端却看不到异常,连接像是被提前断开", "nginx-upstream-502", "semantic"),
    _q("对外回调全线中断,客户端说我们的加密身份不可信了", "ssl-cert-expired", "semantic"),
    _q("搜索服务尾延迟周期性抖动,像是被什么暂停卡住了", "jvm-long-gc-pause", "semantic"),
]

KINDS = ("exact", "lexical", "semantic")


def _hit_id(hit) -> str:
    return pathlib.Path(hit.get("file", "")).stem


def evaluate(backend, k: int = K) -> dict:
    """对全部 QUERIES 跑检索,返回 overall / by_kind 指标 + 逐条结果。"""
    rows = []
    for item in QUERIES:
        res = backend.search(item["query"], limit=k) or {"mode": "none", "hits": []}
        hit_ids = [_hit_id(h) for h in res.get("hits", [])[:k]]
        rank = hit_ids.index(item["expected"]) + 1 if item["expected"] in hit_ids else 0
        rows.append(
            {
                "query": item["query"],
                "kind": item["kind"],
                "expected": item["expected"],
                "mode": res.get("mode", "none"),
                "rank": rank,  # 0 = 未命中
                "hit_at_1": rank == 1,
                "hit_at_k": rank > 0,
                "rr": (1.0 / rank) if rank else 0.0,
            }
        )

    def _agg(subset):
        n = len(subset)
        if not n:
            return {"n": 0, "recall@1": 0.0, f"recall@{k}": 0.0, "mrr": 0.0}
        return {
            "n": n,
            "recall@1": round(sum(r["hit_at_1"] for r in subset) / n, 4),
            f"recall@{k}": round(sum(r["hit_at_k"] for r in subset) / n, 4),
            "mrr": round(sum(r["rr"] for r in subset) / n, 4),
        }

    by_kind = {kind: _agg([r for r in rows if r["kind"] == kind]) for kind in KINDS}
    return {"k": k, "overall": _agg(rows), "by_kind": by_kind, "rows": rows}


def build_sandbox_backend(db_path):
    """把固定语料索引进一个隔离的 SQLite 沙箱;FTS5 不可用时返回 None。"""
    from .sqlite_backend import SqliteSearch

    b = SqliteSearch(db_path=db_path)
    if not b.available():
        return None
    for c in CORPUS:
        b.index_case(c)
    return b


def run_eval(k: int = K) -> dict:
    """Web/CLI 入口:在临时 SQLite 沙箱里评测,绝不触碰生产检索索引。

    返回的报告在 evaluate() 结果上追加:ok、backend、语料/查询规模、命中模式分布、耗时。
    """
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="llm_wiki_eval_") as tmp:
        backend = build_sandbox_backend(pathlib.Path(tmp) / "eval.db")
        if backend is None:
            return {
                "ok": False,
                "reason": "当前 sqlite3 不支持 FTS5 + trigram,无法评测。",
                "backend": "sqlite",
            }
        report = evaluate(backend, k)
        modes: dict[str, int] = {}
        for r in report["rows"]:
            modes[r["mode"]] = modes.get(r["mode"], 0) + 1
        report.update(
            {
                "ok": True,
                "backend": "SQLite · FTS5(评测沙箱)",
                "corpus_size": len(CORPUS),
                "query_count": len(QUERIES),
                "modes": modes,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return report


def _print_report(report) -> None:
    k = report["k"]
    print(f"\n检索评测基线  (k={k}, 语料 {report.get('corpus_size', len(CORPUS))} 案例, "
          f"查询 {report.get('query_count', len(QUERIES))} 条, 后端 {report.get('backend', '?')})")
    print("-" * 60)
    print(f"{'分组':<10}{'n':>4}{'recall@1':>11}{'recall@'+str(k):>11}{'MRR':>9}")
    for kind in KINDS:
        a = report["by_kind"][kind]
        print(f"{kind:<10}{a['n']:>4}{a['recall@1']:>11.2f}{a['recall@'+str(k)]:>11.2f}{a['mrr']:>9.2f}")
    o = report["overall"]
    print("-" * 60)
    print(f"{'overall':<10}{o['n']:>4}{o['recall@1']:>11.2f}{o['recall@'+str(k)]:>11.2f}{o['mrr']:>9.2f}")
    modes = report.get("modes") or {}
    print("命中模式分布:", ", ".join(f"{m}={n}" for m, n in sorted(modes.items())))
    miss = [r for r in report["rows"] if not r["hit_at_k"]]
    if miss:
        print(f"未命中(top-{k}): " + ", ".join(f"{r['expected']}[{r['kind']}]" for r in miss))


def main() -> None:
    import os

    if os.environ.get("EVAL_BACKEND", "sqlite").lower() == "mysql":
        from .mysql_backend import MySQLSearch

        b = MySQLSearch()
        if not b.available():
            raise SystemExit("MySQL 后端不可用,请检查 config.yaml 的 storage.mysql 配置。")
        for c in CORPUS:
            b.index_case(c)
        report = evaluate(b, K)
        report["backend"] = b.label()
    else:
        report = run_eval(K)
        if not report["ok"]:
            raise SystemExit(report["reason"])
    _print_report(report)


if __name__ == "__main__":
    main()
