# 故障案例

已复核或待复核的单次故障案例。每个案例保留 signatures、sources 与解决方案。

* [DB 连接超时 / 连接池耗尽导致接口大面积 500](db-connection-timeout.md) - order-service 高峰期因慢查询长期占用 HikariCP 连接,导致连接池耗尽并批量 500。
