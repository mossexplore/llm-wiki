# ingest 操作:把原始记录沉淀为结构化案例

## 何时运行
有新的故障被排查/解决时:工单关闭、postmortem 完成、或一段 agent 帮忙定位的对话结束。

## 流程
1. **存档原始记录到 raw/**(不可变):把原始工单/对话/笔记原样存为
   `raw/sources/<日期>-<标识>.md`,**之后永不修改**。这是综合出错时的回溯依据。
2. **LLM 生成 wiki 案例**:对该原始记录运行下方提示,产出结构化案例,
   落到 `wiki/cases/_drafts/<slug>.md`,status 一律先 `draft`。
3. **复核升级**:人工(或更严格的 LLM 二次校验)确认无误后,把 status 改为 `verified`
   并移出 `_drafts/`。这是必经的质量门。

## 保守模式(排查领域默认)
- **一条原始记录 → 一个案例**,不做跨案例自动合并/改写。
- 只有人工明确发起时,才做 `concepts/` 综合页或案例合并。
- 这样把"LLM 综合改错解决方案"的风险降到最低。

## LLM 抽取提示(供 scripts/ingest.py 或手动使用)
```
你是日志排查知识库的整理助手。把下面的原始排查记录整理成结构化案例,只输出 JSON:
- title / category / components / background / diagnosis / solution
- signatures: 用户最可能粘贴的报错原文、异常类全名、错误码。
  【必须原文照搬,不得改写、翻译或概括】—— 这是检索命中的命门。
信息缺失就留空,绝不编造解决方案或报错串。
原始记录:
---
{raw}
---
```

## 护栏
- signatures 原文照搬,综合/改写阶段也不得动。
- 一切入库先 draft,经复核才 verified。
- 案例 frontmatter 的 `sources` 必须指回 raw/ 对应文件,保证可溯源。
