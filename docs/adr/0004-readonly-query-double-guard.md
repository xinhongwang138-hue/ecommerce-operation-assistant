# 0004. 只读查询双护栏

- 状态：Accepted
- 日期：2026-10-30
- 影响范围：`api/app/dataplatform/warehouse.py`、`POST /data-platform/query`

## 背景

「让我自己写 SQL 查数」是数据中台最高频的诉求，也是风险最大的入口。风险有三类：

1. 写坏仓：`DROP` / `CREATE` / `INSERT` 覆盖掉刚跑好的表；
2. 读越权数据：查别的租户的表，或者读服务器上的任意文件；
3. 拖垮服务：`SELECT * FROM` 亿行表、笛卡尔积、无 `LIMIT`。

只靠 SQL 文本校验挡不住第 3 类也必须挡不住「聪明的绕过」，只靠数据库权限又挡不住 `read_csv('/etc/passwd')` 这类扩展函数。

## 决策

两层独立的护栏，任一层单独失效都还有另一层：

**第一层，SQL 文本护栏（`guard_select`）**

- 去掉注释后必须**单条**语句，首关键字只能是 `SELECT` 或 `WITH`；
- 剥掉 `WITH` 里的 CTE 名，剩余出现的表名必须带 `ods_ / dwd_ / dws_ / ads_ / meta_` 前缀白名单；
- 禁用关键字：`attach`、`detach`、`copy`、`install`、`load`、`export`、`import`、`read_csv`、`read_parquet`、`read_json`、`pragma`、`duckdb_settings`、`system`；
- 外层强制包一层 `SELECT * FROM (用户 SQL) AS _guarded LIMIT n+1`，多取一行用来判断是否被截断。

**第二层，连接级只读**

```python
duckdb.connect(path, read_only=True)
```

## 后果

- 得到：即使文本护栏被绕过，DuckDB 也会在写操作上直接报错；反过来即使有人把连接开成可写，文本护栏仍然拦着。测试 `test_readonly_connection_rejects_write` 专门验证第二层单独生效。
- 付出：文本护栏必然有误杀（比如列名叫 `system_name` 会被 `system` 规则命中）。当前选择是「宁可误杀」——报错信息里说明命中了哪条规则，用户改个写法即可。
- 约束：`n` 默认 500（`WAREHOUSE_QUERY_MAX_ROWS`），返回体里带 `truncated: true`，不静默截断。
