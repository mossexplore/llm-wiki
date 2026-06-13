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
├── scripts/
│   ├── ingest.py            入库:原始记录 → raw 存档 + OpenAI 生成 draft 案例
│   └── query.py             检索:粘一段报错 → 用 signatures 反向匹配相似案例(零依赖)
├── config.example.yaml      OpenAI 配置模板(复制为 config.yaml 填密钥;后者不入库)
└── requirements.txt         入库依赖(pyyaml + openai;检索无需依赖)
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

## 环境准备(仅入库需要)

```bash
pip install -r requirements.txt        # pyyaml + openai
cp config.example.yaml config.yaml     # 复制配置模板
# 编辑 config.yaml 填入真实值(此文件已被 .gitignore 排除,不会提交):
#   openai:
#     api_key: "sk-..."                 # 必填
#     base_url: "https://api.openai.com/v1"  # 可选,走代理/Azure/本地网关时改
#     model: "gpt-4o"                   # 可选
```

> 检索(`query.py`)零依赖,克隆即用,无需安装与配置。

---

## 一、如何写入知识(ingest)

**触发时机**:一个故障被排查/解决后(工单关闭、postmortem 写完、或一段帮你定位的对话结束)。

**① 跑入库脚本** —— 把原始记录(工单/Slack 线程/排查笔记,任意文本)喂进去:

```bash
python scripts/ingest.py 笔记.txt              # --id 缺省,用时间戳自动命名 raw 文件
cat 笔记.txt | python scripts/ingest.py -       # 管道喂入同理
python scripts/ingest.py 笔记.txt --id INC-5678 # 如有工单号也可手动指定
```

脚本自动做两件事:
- 原文**原样**存档到 `raw/sources/<日期>-<标识>.md`(不可变层,永不改/删);
- 调 OpenAI 抽取成结构化案例,落到 `wiki/cases/_drafts/<slug>.md`,`status: draft`。

**② 人工复核(护栏①,不可省)** —— draft 还不是正式案例。打开草稿核对两处:
- `signatures`(报错原文)必须与原始记录一字不差,**不得改写/翻译**(护栏②);
- `solution` 解决方案是否准确。

确认无误后升级为正式案例:

```bash
# 1) 编辑文件,把 status: draft 改成 status: verified
# 2) 移出暂存区:
git mv wiki/cases/_drafts/<slug>.md wiki/cases/<slug>.md
```

> 不想用 LLM?也可手写:照 `wiki/cases/db-connection-timeout.md` 的模板(frontmatter + 问题背景/定位过程/解决方案三段)手写案例,原文存进 `raw/sources/` 并让 `sources` 字段指回去。

---

## 二、如何检索知识(query)

**① 一条命令检索(推荐)** —— 直接把整段报错粘进去,无需剥日志、无需记 `rg`:

```bash
python scripts/query.py "2026-06-13 15:10:33 ERROR HikariPool-1 - Connection is not available, request timed out after 30007ms"
cat error.log | python scripts/query.py -        # 从文件/管道读
```

输出按三种情形:
- **精确命中** → 打印案例标题、命中的 signature、可信度标注(verified/draft)、和「解决方案」全文;
- **可能相关**(token 有重合但 signature 未精确命中)→ 列出候选,标注"需人工判断,勿照搬";
- **无命中** → 走门控:明确告知"暂无相关案例",**绝不编造**,并提示排查后用 `ingest.py` 入库。

> 原理:不去日志里猜锚点,而是拿每个案例自己精选的 `signatures` 反向匹配你的日志——带时间戳/毫秒数等噪声完全不影响命中。

**② 接进 AI agent** —— 把 `SKILL.md` 装进你的 agent,并在系统提示里写死"凡日志/报错/故障问题必须先查本知识库"。agent 会自动走 SKILL.md 的 query 流程作答。

**③ 加 QMD 语义层(可选)** —— 当你只有"症状描述"而非精确报错串时,`query.py`/`rg` 会漏。装 QMD 指向 `wiki/` 目录、注册其 MCP server 给 agent,即可用自然语言召回相似案例;新增案例自动增量索引。

---

## 三、定期体检(lint)

入库后或定期跑,查重复 signatures / 缺字段 / 滞留草稿 / 断链 / 孤立 raw(规则见 `operations/lint.md`)。lint 只报告不改内容,问题仍走 ingest/复核流程修。

---

## 整体闭环

```
新故障解决 ──ingest.py──▶ raw/(原文存档) + wiki/cases/_drafts/(draft)
                                    │
                               人工复核(改 verified + 移出 _drafts)
                                    ▼
                             wiki/cases/(正式案例)
                                    │
贴报错查询 ◀──query.py / agent / QMD─┘
                  无命中 → 明说"暂无案例" → 排查后回到 ingest,形成闭环
```
