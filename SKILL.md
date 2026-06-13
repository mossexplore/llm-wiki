---
name: log-troubleshooting-wiki
description: >-
  日志报错与线上故障的定位知识库(LLM Wiki 形态)。当用户提供日志报错、异常堆栈、错误码,
  或描述任何系统异常现象(接口报错、服务超时、内存溢出、重启、性能下降等)并希望定位原因或
  获取解决方案时,务必使用本 skill。只要涉及排查日志、定位故障或贴出报错,即使没明说"查知识库",
  也要先用本 skill 检索 wiki/ 下的已归档案例。
---

# 日志问题定位知识库(LLM Wiki)

本知识库采用 LLM Wiki 三层结构,**没有需要手工维护的索引**:

```
raw/      不可变原始记录(工单/对话存档),永不修改,综合出错时回溯用
wiki/     LLM 生成的结构化知识(单点真相源,agent 检索此层)
          ├── cases/      具体故障案例(三段式 + frontmatter,signatures 为检索锚点)
          └── concepts/   跨案例综合的通用规律(辅助排查直觉,不替代具体案例)
schema    即本 SKILL.md:定义结构、检索规则、维护操作
```

新增知识只需往 `raw/` 投原始记录、由 ingest 生成 `wiki/cases/` 文件即可,
**本文件永不需要手改**。

## query 操作:检索与作答(按序执行)

1. **提取特征**:从用户输入抽取报错原文、异常类全名、错误码、组件/服务名。
2. **检索 wiki/cases/**(优先精确,再退语义):
   - 精确:对报错原文串做关键字检索,如 `rg -l -i "Connection is not available" wiki/cases/`
     (可对多个候选串分别检索;日志报错多为确定字符串,精确检索命中率高)
   - 语义:关键字无命中时,用 QMD 检索层(见下),输入用户的症状描述。
3. **读取命中案例**,优先依据其「解决方案」作答;场景不完全一致时,参考「定位过程」
   及关联 `concepts/` 页建立排查思路。
4. **命中门控**:检索无任何命中 → 明确告知"知识库中暂无相关案例",**绝不编造解决方案**。
5. **可信度标注**:`status: verified` 优先采用;命中 `draft` 须标注"该案例尚未复核,仅供参考";
   引用 `concepts/` 综合内容时,若其 confidence 非 high,提示"通用规律,需结合实际验证"。

## 检索层:QMD

推荐用 QMD(本地 markdown 搜索引擎,BM25/向量混合 + LLM 重排,提供 MCP server)作为语义检索层:
- 将 QMD 指向 `wiki/` 目录,注册其 MCP server 到你的 agent。
- agent 调用 QMD 的检索工具(传入用户症状描述)获取候选案例,再走上面第 3、4、5 步。
- QMD 自动跟踪 `wiki/` 变化并增量更新索引,**新增案例无需手工重建索引**。
- 若环境暂不接 QMD,仅用第 2 步的 `rg` 关键字检索也能覆盖绝大多数"粘报错"场景。

## 维护操作

- **ingest**(新增知识):见 `operations/ingest.md` / `scripts/ingest.py`。原始记录入 `raw/`,
  LLM 生成 `wiki/cases/` 草稿(status=draft)。
- **lint**(健康检查):见 `operations/lint.md`。检查重复 signatures、缺字段、滞留草稿、孤立案例、低置信区域。
- 两道护栏始终生效:① draft→verified 需复核;② signatures 原文不可改写。
