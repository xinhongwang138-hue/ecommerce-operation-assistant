"""质量校验：每条规则只写"违规长什么样"，通过与否由行数决定。

统一成 violation SQL 的好处是新增规则不用改执行器，也让每条规则都能单独
拿到仓里跑一遍复现——`SELECT count(*) FROM (<违规 SQL>)` 就是判定式。

severity 分两档：critical 表示这个数不能再往下游走（口径被污染），
warning 表示"值得看一眼但业务能带着它继续跑"。混成一档的结果是所有人
都学会忽略告警。
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass(frozen=True)
class CheckSpec:
    key: str
    name: str
    layer: str
    table: str
    severity: str
    expectation: str
    sql: str


@dataclass(frozen=True)
class CheckResult:
    key: str
    name: str
    layer: str
    table: str
    severity: str
    observed: int
    passed: bool
    expectation: str


CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        key="ods_performance_unique",
        name="经营明细主键唯一",
        layer="ODS",
        table="ods_performance",
        severity="critical",
        expectation="同一 performance_records.id 在 ODS 只能出现一次",
        sql="SELECT id FROM ods_performance GROUP BY id HAVING count(*) > 1",
    ),
    CheckSpec(
        key="perf_spend_non_negative",
        name="消耗不为负",
        layer="DWD",
        table="dwd_perf_fact",
        severity="critical",
        expectation="spend >= 0（负数通常来自退款回冲被错记成消耗）",
        sql="SELECT performance_id FROM dwd_perf_fact WHERE spend < 0",
    ),
    CheckSpec(
        key="perf_clicks_within_impressions",
        name="点击不超过曝光",
        layer="DWD",
        table="dwd_perf_fact",
        severity="critical",
        expectation="clicks <= impressions（否则 CTR 会大于 100%）",
        sql="SELECT performance_id FROM dwd_perf_fact WHERE clicks > impressions",
    ),
    CheckSpec(
        key="sku_available_non_negative",
        name="可售量不为负",
        layer="DWD",
        table="dwd_sku_dim",
        severity="critical",
        expectation="quantity - locked_quantity >= 0；为负即超卖",
        sql="SELECT sku_id FROM dwd_sku_dim WHERE available_qty < 0",
    ),
    CheckSpec(
        key="perf_product_exists",
        name="明细能挂上商品维",
        layer="DWD",
        table="dwd_perf_fact",
        severity="critical",
        expectation="每条经营明细的 product_id 都能在 dwd_product_dim 找到",
        sql="""
            SELECT f.performance_id
            FROM dwd_perf_fact f
            LEFT JOIN dwd_product_dim d ON d.product_id = f.product_id
            WHERE d.product_id IS NULL
        """,
    ),
    CheckSpec(
        key="ops_summary_single_row",
        name="总览表恒为单行",
        layer="ADS",
        table="ads_ops_summary",
        severity="critical",
        expectation="ads_ops_summary 恰好 1 行，否则所有读指标的接口都会拿到多值",
        sql="SELECT 1 AS violation WHERE (SELECT count(*) FROM ads_ops_summary) <> 1",
    ),
    CheckSpec(
        key="ops_summary_gmv_present",
        name="总览成交额非空",
        layer="ADS",
        table="ads_ops_summary",
        severity="critical",
        expectation="gmv_7d 不为 NULL（窗口无数据时也应是 0）",
        sql="SELECT 1 AS violation WHERE (SELECT gmv_7d FROM ads_ops_summary) IS NULL",
    ),
    CheckSpec(
        key="product_store_exists",
        name="商品能挂上店铺",
        layer="ODS",
        table="ods_products",
        severity="warning",
        expectation="store_id 非空的商品应在 ods_stores 中存在",
        sql="""
            SELECT id FROM ods_products
            WHERE store_id IS NOT NULL
              AND store_id NOT IN (SELECT id FROM ods_stores)
        """,
    ),
    CheckSpec(
        key="ods_freshness",
        name="仓内数据新鲜度",
        layer="ODS",
        table="ods_stores",
        severity="warning",
        expectation="最近一次抽取在 48 小时内；超期说明跑批停了，页面会显示旧数",
        sql="""
            SELECT 1 AS violation
            WHERE EXISTS (SELECT 1 FROM ods_stores)
              AND (SELECT max(_extracted_at) FROM ods_stores)
                  < CAST(now() AS TIMESTAMP) - INTERVAL '2 days'
        """,
    ),
)


def run_checks(con: duckdb.DuckDBPyConnection) -> list[CheckResult]:
    results: list[CheckResult] = []
    for spec in CHECKS:
        observed = con.execute(f"SELECT count(*) FROM ({spec.sql}) AS violations").fetchone()[0]
        results.append(
            CheckResult(
                key=spec.key,
                name=spec.name,
                layer=spec.layer,
                table=spec.table,
                severity=spec.severity,
                observed=int(observed),
                passed=int(observed) == 0,
                expectation=spec.expectation,
            )
        )
    return results
