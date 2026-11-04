# 架构决策记录（ADR）

一条 ADR 只记一个「当时为什么这么定」。结论可能变，所以不删旧文，只新增状态为 `Superseded` 的替代条目。

| 编号 | 标题 | 状态 |
|---|---|---|
| [0001](./0001-analysis-warehouse-separated-from-oltp.md) | 分析仓与业务库物理分离 | Accepted |
| [0002](./0002-metric-single-source-of-truth.md) | 指标口径单一真源 + 跑批快照 | Accepted |
| [0003](./0003-lineage-from-declaration.md) | 血缘来自建表声明，不解析 SQL | Accepted |
| [0004](./0004-readonly-query-double-guard.md) | 只读查询双护栏 | Accepted |
| [0005](./0005-tenant-isolation-by-file.md) | 租户隔离落实到仓文件 | Accepted |
| [0006](./0006-full-rebuild-no-incremental.md) | 全量重算，不做增量 | Accepted |
| [0007](./0007-model-sql-is-untrusted-input.md) | 模型写的 SQL 当不可信输入 | Accepted |
| [0008](./0008-column-notes-only-where-names-lie.md) | 字段释义只登记「名字会骗人的列」 | Accepted |
| [0009](./0009-chunking-and-hybrid-retrieval.md) | 切片与检索：向量存 JSON、两路名次做 RRF | Accepted |
| [0010](./0010-self-hosted-rag-coexists-with-external-kb.md) | 自建 RAG 与外部智库并存，不互相替代 | Accepted |
| [0011](./0011-assessment-rules-with-redline-veto.md) | 考核评分：规则引擎 + 红线一票否决 | Accepted |
| [0012](./0012-assessment-snapshot-idempotent-import.md) | 考核只读快照：导入幂等、重算显式 | Accepted |
| [0013](./0013-cs-three-track-routing-and-refusal-floor.md) | 客服三轨道分流与拒答阈值 | Accepted |
| [0014](./0014-faq-before-retrieval.md) | FAQ 排在检索之前：确定性知识不被排序影响 | Accepted |
| [0015](./0015-rules-then-llm-with-quote-verification.md) | 合同审阅：规则打分 + 模型复核，两段分开 | Accepted |
| [0016](./0016-approval-is-audit-not-automation.md) | 审批只留痕，不做自动通过/驳回 | Accepted |
| [0017](./0017-clip-finder-picks-not-cuts.md) | 直播切片只挑片段，不出成片 | Accepted |
| [0018](./0018-finetune-prepares-data-not-training.md) | 微调平台只准备数据，不执行训练 | Accepted |

## 模板

```markdown
# NNNN. 标题

- 状态：Proposed / Accepted / Superseded by NNNN
- 日期：YYYY-MM-DD
- 影响范围：涉及模块

## 背景
约束是什么，试过什么，为什么不够。

## 决策
一句话说清选了哪条路。

## 后果
得到了什么，付出了什么，什么情况下要重新考虑。
```
