# 0002. 指标口径单一真源 + 跑批快照

- 状态：Accepted
- 日期：2026-10-30
- 影响范围：`api/app/dataplatform/metrics.py`、`meta_metrics` 表

## 背景

「ROAS 是多少」这个问题的答案，通常随提问位置变化：看板写 `SUM(gmv)/SUM(spend)`，导出 CSV 的脚本写 `AVG(日 roas)`，另一个报表用 `SUM(gmv)/SUM(clicks)`。三个数都对不上，谁也没错——只是没定义。

常见解法是「写进文档」，但文档不参与计算，改了表结构不会提醒你改文档。

## 决策

口径是**代码里的声明**，不是文档里的描述：

```python
Metric(key="roas", name="ROAS", unit="倍",
       definition="每花 1 元广告带来多少成交额",
       formula="SUM(gmv) / NULLIF(SUM(spend), 0)",
       source_table="ads_ops_daily",
       sql="...")
```

- `GET /metrics` 直接把这份声明返回给前端渲染「指标口径」表——页面上看到的口径和实际执行的 SQL 是同一份代码。
- 每次跑批把全部口径写入 `meta_metrics` 快照。改了口径，历史快照还原样留着，可以回答「上周那份报表用的什么口径」。

## 后果

- 得到：口径只有一个定义点；口径变更留下痕迹；新人读 `metrics.py` 就等于读口径文档。
- 付出：加指标必须改代码 + 重新跑批，不能在 BI 工具里自助加。这是**故意**的——自助加指标正是口径分裂的来源。
- 配套约束：比率一律 `SUM(分子)/NULLIF(SUM(分母),0)`，禁止 `AVG(比率)`。窗口右端取仓内 `max(date)` 而非 `CURRENT_DATE`，否则补录数据后看板会显示成「近 7 天零消耗」。
