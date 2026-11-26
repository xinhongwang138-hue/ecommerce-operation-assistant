# 0006. 全量重算，不做增量

- 状态：Accepted
- 日期：2026-10-30
- 影响范围：`api/app/dataplatform/pipeline.py`

## 背景

跑批的每一层都可以做成增量：ODS 按 `updated_at` 拉新增、DWD 按主键 upsert、DWS/ADS 按日期分区重算。省资源，但也引入一整套新问题：

- 源库的**物理删除**在增量里永远同步不过来（流水表看不出「这行没了」）；
- 迟到数据（补录上周）改了历史分区，下游要不要连带重算？改一条要重算多久？
- 增量一旦算错，错误会被后续增量继承，越滚越大。

## 决策

不做增量。每一层都是 `CREATE OR REPLACE`，一次跑批整层覆盖，事务保护，失败整体回滚。

```python
con.execute("BEGIN")
try:
    for step in all_steps():
        con.execute(step.sql)
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
```

## 后果

- 得到：跑完的仓**一定等于**源库当前状态，没有「增量漂移」这类需要专门排查的故障。补录数据、物理删除、口径变更，全部靠重跑解决，不需要写补偿逻辑。
- 付出：跑批时长随数据量线性涨。当前实测：几千行量级 `467 ms`（真实 MySQL 库，21 张表 + 9 条校验），十万行内仍是秒级，可以同步 HTTP 调用直接返回。
- 重新考虑的触发条件：抽取量超过十万行，或跑批时长超过 30 秒。届时改「按日期分区重算 + 队列异步」，`Step` 声明结构不变，只换执行器——这也是把 `depends_on` 写进声明而不是靠 SQL 解析的收益之一。
