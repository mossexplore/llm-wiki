---
id: db-connection-timeout
title: DB 连接超时 / 连接池耗尽导致接口大面积 500
category: 数据库
status: verified              # draft(自动入库,待复核) | verified(已人工复核)
confidence: high              # high | medium | low —— 供 lint 浮现待补强区域
signatures:                   # 检索锚点:报错原文/异常类全名/错误码,【原文照搬,综合时不可改写】
  - "HikariPool-1 - Connection is not available, request timed out"
  - "org.springframework.jdbc.CannotGetJdbcConnectionException"
  - "Connection timed out"
components: [order-service, MySQL, HikariCP]
created: 2024-05-10
sources:                      # 溯源:指回 raw/ 不可变层
  - raw/sources/2024-05-10-INC-1234.md
related:                      # 交叉链接(wiki 层的综合能力,保守使用)
  - wiki/concepts/connection-pool-exhaustion.md
---

## 问题背景
大促高峰期,order-service 部分接口大量报 500,RT 从 80ms 飙到 5s+。流量数倍于平时,
DB 实例规格未变,应用侧 HikariCP maximumPoolSize=20。

## 定位过程
1. 应用日志持续刷连接池获取超时(见 signatures),初判连接不够用。
2. 连接池监控:active 长时间顶满 20,pending 堆积 → 确认连接被占光。
3. `SHOW PROCESSLIST`:大量同一条 SELECT 在执行,单条耗时数秒。
4. 慢查询日志:该 SELECT 未命中索引、全表扫描(数百万行),长期占用连接、释放不掉,
   高峰一来连接池被耗尽。

## 解决方案
1. 根因修复:给查询字段加联合索引,SELECT 由 4s+ 降到毫秒级。
2. 容量调整:maximumPoolSize 20 → 50(结合 DB 最大连接数评估,勿压垮 DB)。
3. 防护兜底:statement timeout 3s,避免个别慢查询长期占用连接。
4. 验证:压测复现高峰流量,active 回落健康水位,500 消失。

## 备注
连接池耗尽常是表象,根因多在慢查询/慢下游/连接泄漏。先定位"谁占着连接不放"再决定是否扩容。
