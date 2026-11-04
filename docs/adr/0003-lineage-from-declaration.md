# 0003. 血缘来自建表声明，不解析 SQL

- 状态：Accepted
- 日期：2026-10-30
- 影响范围：`api/app/dataplatform/pipeline.py`、`meta_lineage` 表

## 背景

血缘（这张 ADS 表由哪几张上游算出来）有两种获取方式：解析 SQL 抽出表名，或在建表时显式声明依赖。

解析 SQL 看起来省事，但真实 SQL 里有 `WITH a AS (...), b AS (SELECT ... FROM a ...)`、有嵌套子查询、有 `FROM (VALUES ...)`、有动态拼的过滤条件。写一个能覆盖这些的解析器不现实，用正则抽表名则会漏、会错，而**错的血缘比没有血缘更有害**——它会让人放心地改掉一张其实有下游依赖的表。

## 决策

依赖在声明里写清楚：

```python
Step(name="build_ads_ops_summary", layer="ADS", target="ads_ops_summary",
     depends_on=("dws_ops_daily", "dws_store_daily", "dws_product_daily"),
     sql=ADS_SQL["ads_ops_summary"])
```

跑批时把 `depends_on` 原样写入 `meta_lineage`，`GET /lineage` 直接读表，不推测。

## 后果

- 得到：血缘 100% 准确；新增表时人必须想一次「我依赖谁」，这个思考本身就是价值；血缘可以跨出 SQL 范畴（比如依赖一张外部导入表）。
- 付出：`depends_on` 写错不会被自动发现。缓解手段是 `tests/test_data_platform_pipeline.py::test_lineage_covers_every_table`——它检查每张非 ODS 表都有入边、每条边指向的表真实存在。
- 附带效果：`Step` 的声明顺序即执行顺序，跑批不需要另做拓扑排序。
