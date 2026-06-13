# lint 操作:知识库健康检查

定期(或每次 ingest 后)运行,维持 wiki/ 的一致性与可检索性。可由脚本或 agent 执行。

## 检查项
1. **重复/冲突 signatures**:多个案例声明了相同报错串 → 可能重复或需合并,人工裁决。
2. **缺字段**:case 缺少 signatures / solution / sources 等必填项 → 标记待补。
3. **滞留草稿**:`_drafts/` 中 status=draft 超过 N 天未复核 → 提醒处理(防止草稿长期被当正式案例)。
4. **断链**:`sources` 指向的 raw/ 文件不存在,或 `related`/`cases` 指向的页不存在 → 修复链接。
5. **孤立 raw**:raw/ 里有原始记录但没有对应 wiki 案例 → 可能漏 ingest。
6. **低置信区域**:汇总 confidence=low 的 case/concept → 浮现"最该补强/复核"的知识点
   (对应 Karpathy LLM Wiki 的"浮现最不确定知识"思想)。

## 输出
一份简短报告:各检查项的命中清单 + 建议动作。不自动改动内容,改动交人工或显式操作,
避免 lint 误伤正确案例。

## 与护栏的关系
lint 是"体检"不是"手术":它发现问题并建议,真正的修改仍走 ingest/复核流程,
确保正确性始终有人或严格校验把关。
