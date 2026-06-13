# log-wiki —— 日志排查知识库(LLM Wiki 形态)

按 Karpathy 的 LLM Wiki 模式构建的、面向 agent 的日志故障定位知识库。
针对"排查领域正确性要求高"做了裁剪:保守入库 + 两道护栏,而非研究版的激进综合。

## 三层结构

```
log-wiki/
├── SKILL.md                 schema 层:结构定义 + query 检索规则 + 维护操作入口(agent 读这个)
├── raw/                     第一层:不可变原始记录,永不修改,综合出错时回溯
│   └── sources/
│       └── 2024-05-10-INC-1234.md
├── wiki/                    第二层:LLM 生成的结构化知识(单点真相源,agent 检索此层)
│   ├── cases/               具体故障案例(三段式 + frontmatter,signatures 为检索锚点)
│   │   └── db-connection-timeout.md
│   │   └── _drafts/         入库草稿暂存(待复核)
│   └── concepts/            跨案例综合的通用规律(辅助直觉,不替代具体案例)
│       └── connection-pool-exhaustion.md
├── operations/              三个操作的定义
│   ├── ingest.md            新增知识:raw 存档 → LLM 生成 draft 案例
│   └── lint.md              健康检查:重复/缺字段/滞留草稿/断链/低置信
└── scripts/
    └── ingest.py            ingest 自动化脚本(接你的 LLM)
```

## 三个操作

- **query**:用户提问时,agent 按 SKILL.md 的流程检索 `wiki/cases/`(精确关键字优先,QMD 语义兜底),
  命中则依「解决方案」作答,无命中明确告知、不编造。
- **ingest**:新故障解决后,原始记录入 `raw/`,LLM 生成 `wiki/cases/_drafts/` 草稿,复核后升 verified。
- **lint**:定期体检知识库一致性,发现问题并建议,不自动改内容。

## 检索层:QMD

推荐用 QMD(本地 markdown 搜索,BM25/向量混合 + LLM 重排,带 MCP server)作语义检索:
1. 安装 QMD,将其指向本仓库的 `wiki/` 目录。
2. 把 QMD 的 MCP server 注册到你的 agent。
3. agent 检索时调用 QMD 工具;新增案例 QMD 自动增量索引,无需手工重建。

暂不接 QMD 时,仅靠 SKILL.md 里的 `rg` 关键字检索也能覆盖绝大多数"粘报错"场景。

## 两道护栏(排查领域必备)

1. **draft → verified 需复核**:自动入库一律先 draft;命中 draft 时 agent 会标注"未复核,仅供参考"。
   一条错误的归档解法可能直接引发事故,这道闸不能省。
2. **signatures 原文不可改**:检索全靠精确报错串命中,ingest/综合阶段都不得改写 signatures。

## 快速上手

1. 把你现有的 markdown 排查文档,按一案例一文件拆进 `wiki/cases/`,套用示例的 frontmatter。
   原始出处一并存进 `raw/sources/`,case 的 `sources` 字段指回去。
2. `scripts/ingest.py` 里接上你的 LLM,之后新故障 `python scripts/ingest.py note.txt --id INC-xxxx` 即可自动入库草稿。
3. 接入 QMD 作检索层,或先用 ripgrep。
4. 把 `SKILL.md` 装进你的 agent;并在 agent 系统提示里写死"凡日志/报错/故障问题必须先查本知识库"。
```
```
