# 0008. 字段释义只登记「名字会骗人的列」

- 状态：Accepted
- 日期：2026-10-30
- 影响范围：`api/app/nl2sql/catalog.py`

## 背景

问数的目录一开始只有两样东西：表名 + 列名类型。实测立刻出问题：

> 问题：Agent 任务失败最多的那天是几号，失败率多少
> 生成的 SQL：`SELECT date, failed, success_rate AS 失败率 FROM dws_agent_daily ...`

列名存在、类型正确、SQL 能跑、结果非空——口径是错的。`success_rate` 是成功率（0–1），不是失败率。这类错误最危险的地方在于**没有任何一环会报错**：护栏放行、DuckDB 执行成功、前端渲染一张好看的表格。

当时有两条路：

1. **建全量数据字典**（每列一句中文注释）。成本高，且实际效果相反：模型对 200 条注释的注意力是平摊的，真正要紧的那条会被稀释。
2. **只登记会骗人的列**。列名本身是诚实的不写；只有"看名字会理解反"的才写。

## 决策

走第 2 条。`_COLUMN_NOTES` 是一张短表，每条都必须能说清「只看列名会怎么错」：

```python
"dws_agent_daily.success_rate": "成功率（0-1），不是失败率；失败率 = 1 - success_rate",
"dwd_perf_fact.spend_missing": "该行没填消耗；spend=0 不代表真的没花钱",
"dwd_perf_fact.roas": "行级 ROI 是 revenue/spend；多行汇总时不能 AVG 它",
```

说不清"会怎么错"的列，不进这张表。

## 后果

- 得到：同一句问题、同一个模型，加了那一行释义后输出从 `success_rate AS 失败率` 变成 `success_rate AS 成功率, 1 - success_rate AS 失败率`（见 `_evidence/INDEX.md` E10）。这是 P03 里唯一一处「模型的自由判断」被真正约束住的地方。
- 得到：这张表现在只有 7 条，一眼能读完，新增时必然会想一次「真的需要吗」。
- 付出：释义是手工维护的，改了语义要记得改它。缓解手段是 `test_catalog_annotates_columns_whose_name_lies` 断言关键条目仍在目录里。
- 没有解决的事：释义只覆盖"名字说谎"，覆盖不了"业务口径分歧"。后者归 `metrics.py`（ADR 0002），不归这里——两个机制管两件事，不合并。
