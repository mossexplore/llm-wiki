---
id: connection-pool-exhaustion
type: concept                 # 概念页:跨案例综合,非单一事故记录
title: 连接池耗尽(通用规律)
description: 连接池耗尽通常是慢查询、慢下游或连接泄漏长期占用连接后的表象。
tags: [连接池, HikariCP, 数据库, 排查规律]
status: verified
confidence: medium            # 概念页由多案例综合而来,默认标 medium,提醒复核
timestamp: 2024-05-10T00:00:00Z
cases:                        # 关联的具体案例(综合来源)
  - wiki/cases/db-connection-timeout.md
---

> 概念页是 wiki 层的"综合"产物,用于沉淀跨多个案例的通用规律。
> **它不替代具体案例**;agent 作答仍以具体 case 的「解决方案」为准,概念页用于建立排查直觉。
> 综合内容一律标注 confidence,且需人工复核后才升 verified。

## 核心规律
连接池耗尽(连接获取超时)几乎总是**表象**,而非根因。真正原因通常是:
- 慢查询/全表扫描长期占用连接(见 `db-connection-timeout`)
- 下游/DB 响应慢,连接迟迟不释放
- 连接泄漏(用完不归还)

## 排查直觉
1. 先看连接池监控:active 是否顶满、pending 是否堆积 → 确认"被占光"。
2. 再定位"谁占着不放":`SHOW PROCESSLIST` + 慢查询日志,而不是先扩池子。
3. 扩 maximumPoolSize 是兜底手段,需评估 DB 最大连接数,避免把压力推给 DB。

## 反模式
无脑调大连接池而不查根因 —— 短期缓解,长期掩盖问题,流量再涨照样耗尽。
