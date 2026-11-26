# 0005. 租户隔离落实到仓文件

- 状态：Accepted
- 日期：2026-10-30
- 影响范围：`api/app/dataplatform/warehouse.py`、`extract.py`

## 背景

业务的租户字段是 `workspace_id`。多租户的常规做法是「同一份存储，每条 SQL 带 `where workspace_id = :ws`」。这在业务库里可行（有 ORM 兜底、有 code review、有 RLS 可用），但在分析仓里格外危险：仓的查询入口允许用户手写 SQL，**任何一条漏掉 `where` 的 SQL 都会同时读到所有人**，而且漏写不会报错，只会安静地多返回几行。

## 决策

隔离落在文件系统层：一个 workspace 一个 `.duckdb` 文件。

```python
def warehouse_path(workspace_id) -> Path:
    return WAREHOUSE_DIR / f"workspace_{int(workspace_id or 0)}.duckdb"
```

每个请求按 `X-Workspace-ID` 解析出 workspace，打开对应文件。抽取阶段仍然带 `workspace_id` 过滤（跨库过滤，`_WORKSPACE_FILTER`；子表通过回连 `products` 判租户），因为业务库里数据是混放的。

## 后果

- 得到：漏写 `where` 的后果从「读到别人的数据」降级为「读到自己的空数据」——错得安全。文件还能单独备份、单独删除、单独拷给客户。
- 付出：跨租户的对比分析做不了（这在当前产品里不是需求）；租户数量多时文件数增多，DuckDB 单文件有句柄与内存开销，需要在数量级上做归档。
- 附带收益：跑批互不干扰，一个租户跑批不会锁住另一个租户的查询。仓内另有按 workspace 的写锁（`_workspace_lock`），防止同一租户并发跑批互相 `CREATE OR REPLACE`。
