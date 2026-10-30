"""四层建模流水线：ODS → DWD → DWS → ADS。

约定三条，写在代码里而不是文档里：

1. **每次跑批整层重算**（`CREATE OR REPLACE`），不做增量。数据量在十万行以内，
   全量重算换来的是"跑完的仓一定等于源库当前状态"，不必解释为什么某行还在。
2. **窗口右端取仓内最新业务日期**，不是 `CURRENT_DATE`。离线数据补录时用今天做右端，
   会让"上周投了十万"显示成"近 7 天零消耗"——这类静默错误比报错更难发现。
3. **ratio 指标一律 `SUM(分子)/NULLIF(SUM(分母), 0)`**，先聚合再相除。
   按行求比值再平均，会被小分母行拉偏（CTR 尤其明显）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import duckdb
from sqlalchemy.orm import Session

from app.dataplatform.extract import EXTRACTED_AT_COLUMN, EXTRACTED_AT_TYPE, SOURCES, extract_rows
from app.dataplatform.warehouse import connect

LAYERS: tuple[str, ...] = ("ODS", "DWD", "DWS", "ADS")

LAYER_LABELS = {
    "ODS": "原始层 · 源表原样落地",
    "DWD": "明细层 · 清洗去重与维度对齐",
    "DWS": "汇总层 · 按主题轻度聚合",
    "ADS": "应用层 · 指标与看板直接取数",
}


@dataclass(frozen=True)
class Step:
    """一次建表动作。depends_on 是血缘的单一来源，不另写一份图。"""

    name: str
    layer: str
    target: str
    depends_on: tuple[str, ...]
    description: str
    sql: str


DWD_SQL: tuple[Step, ...] = (
    Step(
        name="build_product_dim",
        layer="DWD",
        target="dwd_product_dim",
        depends_on=("ods_products", "ods_stores"),
        description="商品维：补店铺名与平台，算毛差",
        sql="""
            CREATE OR REPLACE TABLE dwd_product_dim AS
            SELECT
                p.id AS product_id,
                p.name AS product_name,
                p.store_id,
                coalesce(s.name, '未关联店铺') AS store_name,
                coalesce(s.platform, p.platform) AS platform,
                p.category,
                p.price,
                p.cost,
                CASE WHEN p.price IS NOT NULL THEN p.price - coalesce(p.cost, 0) END AS gross_margin
            FROM ods_products p
            LEFT JOIN ods_stores s ON s.id = p.store_id
        """,
    ),
    Step(
        name="build_sku_dim",
        layer="DWD",
        target="dwd_sku_dim",
        depends_on=("ods_skus", "ods_inventory", "dwd_product_dim"),
        description="SKU 维：挂库存快照，标低库存",
        sql="""
            CREATE OR REPLACE TABLE dwd_sku_dim AS
            SELECT
                k.id AS sku_id,
                k.product_id,
                k.sku_code,
                k.name AS sku_name,
                k.price,
                coalesce(i.quantity, 0) AS quantity,
                coalesce(i.locked_quantity, 0) AS locked_quantity,
                -- 不做 greatest(...,0)：负的可售量代表超卖，是质量校验要抓的信号，不能被抹平。
                coalesce(i.quantity, 0) - coalesce(i.locked_quantity, 0) AS available_qty,
                coalesce(i.warning_threshold, 0) AS warning_threshold,
                coalesce(i.quantity, 0) - coalesce(i.locked_quantity, 0)
                    <= coalesce(i.warning_threshold, 0) AS is_low_stock
            FROM ods_skus k
            LEFT JOIN ods_inventory i ON i.sku_id = k.id
        """,
    ),
    Step(
        name="build_perf_fact",
        layer="DWD",
        target="dwd_perf_fact",
        depends_on=("ods_performance", "dwd_product_dim"),
        description="经营明细：同键重复导入保留最新一条",
        sql="""
            CREATE OR REPLACE TABLE dwd_perf_fact AS
            WITH ranked AS (
                SELECT
                    r.*,
                    row_number() OVER (
                        PARTITION BY r.product_id, r.period_start, r.period_end
                        ORDER BY r.id DESC
                    ) AS rn
                FROM ods_performance r
            )
            SELECT
                r.id AS performance_id,
                r.product_id,
                d.store_id,
                d.store_name,
                d.platform,
                r.period_start,
                r.period_end,
                coalesce(r.impressions, 0) AS impressions,
                coalesce(r.clicks, 0) AS clicks,
                coalesce(r.conversions, 0) AS conversions,
                coalesce(r.spend, 0) AS spend,
                coalesce(r.revenue, 0) AS revenue,
                r.spend IS NULL AS spend_missing,
                CAST(r.clicks AS DOUBLE) / NULLIF(r.impressions, 0) AS ctr,
                CAST(r.conversions AS DOUBLE) / NULLIF(r.clicks, 0) AS cvr,
                r.revenue / NULLIF(r.spend, 0) AS roas
            FROM ranked r
            LEFT JOIN dwd_product_dim d ON d.product_id = r.product_id
            WHERE r.rn = 1
        """,
    ),
    Step(
        name="build_asset_fact",
        layer="DWD",
        target="dwd_asset_fact",
        depends_on=("ods_generated_assets", "ods_creative_plans", "dwd_product_dim"),
        description="素材事实：挂商品与方案类型",
        sql="""
            CREATE OR REPLACE TABLE dwd_asset_fact AS
            SELECT
                a.id AS asset_id,
                a.product_id,
                d.product_name,
                a.creative_plan_id,
                c.type AS plan_type,
                a.asset_type,
                a.status,
                a.rating,
                a.created_at
            FROM ods_generated_assets a
            LEFT JOIN ods_creative_plans c ON c.id = a.creative_plan_id
            LEFT JOIN dwd_product_dim d ON d.product_id = a.product_id
        """,
    ),
    Step(
        name="build_task_fact",
        layer="DWD",
        target="dwd_task_fact",
        depends_on=("ods_agent_tasks",),
        description="Agent 任务事实：成功率与耗时",
        sql="""
            CREATE OR REPLACE TABLE dwd_task_fact AS
            SELECT
                t.id AS task_id,
                t.workspace_id,
                t.workflow_key,
                t.status,
                t.risk_level,
                t.status = 'completed' AS is_success,
                t.created_at,
                t.finished_at,
                date_diff('minute', t.created_at, t.finished_at) AS duration_minutes
            FROM ods_agent_tasks t
        """,
    ),
)

DWS_SQL: tuple[Step, ...] = (
    Step(
        name="build_store_daily",
        layer="DWS",
        target="dws_store_daily",
        depends_on=("dwd_perf_fact",),
        description="店铺日汇总",
        sql="""
            CREATE OR REPLACE TABLE dws_store_daily AS
            SELECT
                period_start AS date,
                store_id,
                store_name,
                platform,
                sum(impressions) AS impressions,
                sum(clicks) AS clicks,
                sum(conversions) AS conversions,
                sum(spend) AS spend,
                sum(revenue) AS revenue,
                CAST(sum(clicks) AS DOUBLE) / NULLIF(sum(impressions), 0) AS ctr,
                CAST(sum(conversions) AS DOUBLE) / NULLIF(sum(clicks), 0) AS cvr,
                sum(revenue) / NULLIF(sum(spend), 0) AS roas
            FROM dwd_perf_fact
            GROUP BY period_start, store_id, store_name, platform
        """,
    ),
    Step(
        name="build_product_daily",
        layer="DWS",
        target="dws_product_daily",
        depends_on=("dwd_perf_fact",),
        description="商品日汇总",
        sql="""
            CREATE OR REPLACE TABLE dws_product_daily AS
            SELECT
                period_start AS date,
                product_id,
                sum(impressions) AS impressions,
                sum(clicks) AS clicks,
                sum(conversions) AS conversions,
                sum(spend) AS spend,
                sum(revenue) AS revenue
            FROM dwd_perf_fact
            GROUP BY period_start, product_id
        """,
    ),
    Step(
        name="build_inventory_snapshot",
        layer="DWS",
        target="dws_inventory_snapshot",
        depends_on=("dwd_sku_dim",),
        description="商品库存快照",
        sql="""
            CREATE OR REPLACE TABLE dws_inventory_snapshot AS
            SELECT
                product_id,
                count(*) AS sku_count,
                sum(available_qty) AS available_qty,
                sum(CASE WHEN is_low_stock THEN 1 ELSE 0 END) AS low_stock_skus
            FROM dwd_sku_dim
            GROUP BY product_id
        """,
    ),
    Step(
        name="build_agent_daily",
        layer="DWS",
        target="dws_agent_daily",
        depends_on=("dwd_task_fact",),
        description="Agent 任务日汇总",
        sql="""
            CREATE OR REPLACE TABLE dws_agent_daily AS
            SELECT
                CAST(created_at AS DATE) AS date,
                count(*) AS task_count,
                sum(CASE WHEN is_success THEN 1 ELSE 0 END) AS succeeded,
                sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                CAST(sum(CASE WHEN is_success THEN 1 ELSE 0 END) AS DOUBLE)
                    / NULLIF(count(*), 0) AS success_rate,
                avg(duration_minutes) AS avg_duration_minutes
            FROM dwd_task_fact
            GROUP BY CAST(created_at AS DATE)
        """,
    ),
)

ADS_SQL: tuple[Step, ...] = (
    Step(
        name="build_ops_daily",
        layer="ADS",
        target="ads_ops_daily",
        depends_on=("dws_store_daily",),
        description="全站日指标",
        sql="""
            CREATE OR REPLACE TABLE ads_ops_daily AS
            SELECT
                date,
                sum(revenue) AS gmv,
                sum(spend) AS spend,
                sum(impressions) AS impressions,
                sum(clicks) AS clicks,
                sum(conversions) AS conversions,
                sum(revenue) / NULLIF(sum(spend), 0) AS roas,
                CAST(sum(clicks) AS DOUBLE) / NULLIF(sum(impressions), 0) AS ctr,
                CAST(sum(conversions) AS DOUBLE) / NULLIF(sum(clicks), 0) AS cvr,
                sum(revenue) / NULLIF(sum(conversions), 0) AS aov
            FROM dws_store_daily
            GROUP BY date
            ORDER BY date
        """,
    ),
    Step(
        name="build_store_kpi",
        layer="ADS",
        target="ads_store_kpi",
        depends_on=("dws_store_daily",),
        description="店铺近 7 天 KPI",
        sql="""
            CREATE OR REPLACE TABLE ads_store_kpi AS
            WITH w AS (SELECT max(date) AS window_end FROM dws_store_daily)
            SELECT
                s.store_id,
                s.store_name,
                s.platform,
                sum(s.revenue) AS gmv_7d,
                sum(s.spend) AS spend_7d,
                sum(s.revenue) / NULLIF(sum(s.spend), 0) AS roas_7d,
                CAST(sum(s.clicks) AS DOUBLE) / NULLIF(sum(s.impressions), 0) AS ctr_7d,
                CAST(sum(s.conversions) AS DOUBLE) / NULLIF(sum(s.clicks), 0) AS cvr_7d
            FROM dws_store_daily s, w
            WHERE w.window_end IS NOT NULL
              AND s.date BETWEEN w.window_end - INTERVAL '6 days' AND w.window_end
            GROUP BY s.store_id, s.store_name, s.platform
        """,
    ),
    Step(
        name="build_product_health",
        layer="ADS",
        target="ads_product_health",
        depends_on=("dwd_product_dim", "dws_product_daily", "dws_inventory_snapshot"),
        description="商品健康度：投放效率 × 库存风险",
        sql="""
            CREATE OR REPLACE TABLE ads_product_health AS
            WITH w AS (SELECT max(date) AS window_end FROM dws_product_daily),
            agg AS (
                SELECT
                    p.product_id,
                    sum(p.revenue) AS gmv_7d,
                    sum(p.spend) AS spend_7d,
                    sum(p.conversions) AS conversions_7d
                FROM dws_product_daily p, w
                WHERE w.window_end IS NOT NULL
                  AND p.date BETWEEN w.window_end - INTERVAL '6 days' AND w.window_end
                GROUP BY p.product_id
            )
            SELECT
                d.product_id,
                d.product_name,
                d.store_name,
                coalesce(a.gmv_7d, 0) AS gmv_7d,
                coalesce(a.spend_7d, 0) AS spend_7d,
                coalesce(a.conversions_7d, 0) AS conversions_7d,
                a.gmv_7d / NULLIF(a.spend_7d, 0) AS roas_7d,
                coalesce(i.sku_count, 0) AS sku_count,
                coalesce(i.low_stock_skus, 0) AS low_stock_skus,
                CASE
                    WHEN coalesce(i.low_stock_skus, 0) > 0 THEN '库存风险'
                    WHEN coalesce(a.gmv_7d, 0) = 0 THEN '无成交'
                    WHEN coalesce(a.spend_7d, 0) > 0
                         AND a.gmv_7d / NULLIF(a.spend_7d, 0) < 1 THEN '投放亏损'
                    ELSE '正常'
                END AS risk_label
            FROM dwd_product_dim d
            LEFT JOIN agg a ON a.product_id = d.product_id
            LEFT JOIN dws_inventory_snapshot i ON i.product_id = d.product_id
        """,
    ),
    Step(
        name="build_ops_summary",
        layer="ADS",
        target="ads_ops_summary",
        depends_on=("ads_ops_daily", "dws_inventory_snapshot", "dws_agent_daily", "dwd_asset_fact"),
        description="全站近 7 天总览单行",
        sql="""
            CREATE OR REPLACE TABLE ads_ops_summary AS
            WITH w AS (SELECT max(date) AS window_end FROM ads_ops_daily),
            perf AS (
                SELECT
                    sum(d.gmv) AS gmv,
                    sum(d.spend) AS spend,
                    sum(d.impressions) AS impressions,
                    sum(d.clicks) AS clicks,
                    sum(d.conversions) AS conversions
                FROM ads_ops_daily d, w
                WHERE w.window_end IS NOT NULL
                  AND d.date BETWEEN w.window_end - INTERVAL '6 days' AND w.window_end
            ),
            inv AS (
                SELECT
                    count(*) AS sku_count,
                    sum(CASE WHEN is_low_stock THEN 1 ELSE 0 END) AS low_stock_skus
                FROM dwd_sku_dim
            ),
            agent AS (
                SELECT
                    sum(a.task_count) AS task_count,
                    sum(a.succeeded) AS succeeded
                FROM dws_agent_daily a, w
                WHERE w.window_end IS NOT NULL
                  AND a.date BETWEEN w.window_end - INTERVAL '6 days' AND w.window_end
            )
            SELECT
                w.window_end AS window_end,
                CASE WHEN w.window_end IS NULL THEN NULL
                     ELSE w.window_end - INTERVAL '6 days' END AS window_start,
                coalesce(perf.gmv, 0) AS gmv_7d,
                coalesce(perf.spend, 0) AS spend_7d,
                perf.gmv / NULLIF(perf.spend, 0) AS roas_7d,
                CAST(perf.clicks AS DOUBLE) / NULLIF(perf.impressions, 0) AS ctr_7d,
                CAST(perf.conversions AS DOUBLE) / NULLIF(perf.clicks, 0) AS cvr_7d,
                perf.gmv / NULLIF(perf.conversions, 0) AS aov_7d,
                CAST(inv.low_stock_skus AS DOUBLE) / NULLIF(inv.sku_count, 0) AS low_stock_ratio,
                CAST(agent.succeeded AS DOUBLE) / NULLIF(agent.task_count, 0) AS agent_success_rate,
                (SELECT count(*) FROM dwd_asset_fact) AS asset_count,
                (SELECT count(*) FROM dwd_product_dim) AS product_count,
                -- 数在册店铺，不能数 ads_store_kpi 的行：那张表只装有投放数据的店铺，
                -- 于是一个有 1 家店但还没录经营数据的租户会显示"商品 1 个 · 店铺 0 个"。
                (SELECT count(DISTINCT store_id) FROM dwd_product_dim) AS store_count
            FROM w, perf, inv, agent
        """,
    ),
)


def ods_steps() -> tuple[Step, ...]:
    """ODS 步骤由抽取规格生成，避免在两张清单里各维护一份表名。"""
    return tuple(
        Step(
            name=f"load_{spec.target}",
            layer="ODS",
            target=spec.target,
            depends_on=(f"mysql.{spec.source_table}",),
            description=spec.description,
            sql="",
        )
        for spec in SOURCES
    )


def all_steps() -> tuple[Step, ...]:
    return ods_steps() + DWD_SQL + DWS_SQL + ADS_SQL


def _ensure_meta(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_runs (
            run_id VARCHAR,
            workspace_id BIGINT,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            duration_ms BIGINT,
            status VARCHAR,
            ods_rows BIGINT,
            tables_built BIGINT,
            checks_passed BIGINT,
            checks_failed BIGINT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_lineage (
            source VARCHAR,
            target VARCHAR,
            step VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_metrics (
            metric_key VARCHAR,
            name VARCHAR,
            unit VARCHAR,
            direction VARCHAR,
            layer VARCHAR,
            source_table VARCHAR,
            definition VARCHAR,
            formula VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_quality (
            run_id VARCHAR,
            check_key VARCHAR,
            name VARCHAR,
            layer VARCHAR,
            table_name VARCHAR,
            severity VARCHAR,
            observed BIGINT,
            passed BOOLEAN,
            expectation VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_tables (
            table_name VARCHAR,
            layer VARCHAR,
            description VARCHAR,
            row_count BIGINT
        )
        """
    )


def _create_ods_table(con: duckdb.DuckDBPyConnection, spec: Any) -> None:
    columns = ",\n                ".join(
        [f"{column.name} {column.duckdb_type}" for column in spec.columns]
        + [f"{EXTRACTED_AT_COLUMN} {EXTRACTED_AT_TYPE}"]
    )
    con.execute(f"CREATE OR REPLACE TABLE {spec.target} (\n                {columns}\n            )")


def _load_ods(con: duckdb.DuckDBPyConnection, db: Session, workspace_id: int | None, extracted_at: datetime) -> int:
    total = 0
    for spec in SOURCES:
        _create_ods_table(con, spec)
        rows = extract_rows(db, spec, workspace_id=workspace_id, extracted_at=extracted_at)
        if rows:
            placeholders = ", ".join(["?"] * (len(spec.columns) + 1))
            con.executemany(f"INSERT INTO {spec.target} VALUES ({placeholders})", rows)
        total += len(rows)
    return total


def _write_lineage(con: duckdb.DuckDBPyConnection, steps: tuple[Step, ...]) -> None:
    con.execute("DELETE FROM meta_lineage")
    edges = [(source, step.target, step.name) for step in steps for source in step.depends_on]
    if edges:
        con.executemany("INSERT INTO meta_lineage VALUES (?, ?, ?)", edges)


def _write_tables(con: duckdb.DuckDBPyConnection, steps: tuple[Step, ...]) -> None:
    con.execute("DELETE FROM meta_tables")
    rows: list[tuple[str, str, str, int]] = []
    for step in steps:
        count = con.execute(f"SELECT count(*) FROM {step.target}").fetchone()[0]
        rows.append((step.target, step.layer, step.description, int(count)))
    con.executemany("INSERT INTO meta_tables VALUES (?, ?, ?, ?)", rows)


@dataclass
class RunResult:
    run_id: str
    workspace_id: int | None
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status: str
    ods_rows: int
    tables_built: int
    checks_passed: int
    checks_failed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "ods_rows": self.ods_rows,
            "tables_built": self.tables_built,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
        }


def run_pipeline(db: Session, workspace_id: int | None) -> RunResult:
    """跑一次全量：抽 ODS → 建 DWD/DWS/ADS → 记血缘/口径 → 质量校验 → 记 meta。"""
    # 延迟导入：quality 依赖 metric 注册表，放模块顶部会形成 pipeline ↔ quality 环。
    from app.dataplatform import quality
    from app.dataplatform.metrics import METRICS

    started = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = f"{started:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    workspace = int(workspace_id or 0)

    with connect(workspace_id) as con:
        _ensure_meta(con)
        con.execute("BEGIN TRANSACTION")
        try:
            ods_rows = _load_ods(con, db, workspace_id, started)

            steps = all_steps()
            for step in steps:
                if step.sql:
                    con.execute(step.sql)

            _write_lineage(con, steps)
            _write_tables(con, steps)

            con.execute("DELETE FROM meta_metrics")
            con.executemany(
                "INSERT INTO meta_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        metric.key,
                        metric.name,
                        metric.unit,
                        metric.direction,
                        metric.layer,
                        metric.source_table,
                        metric.definition,
                        metric.formula,
                    )
                    for metric in METRICS.values()
                ],
            )

            checks = quality.run_checks(con)
            con.execute("DELETE FROM meta_quality")
            con.executemany(
                "INSERT INTO meta_quality VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        check.key,
                        check.name,
                        check.layer,
                        check.table,
                        check.severity,
                        check.observed,
                        check.passed,
                        check.expectation,
                    )
                    for check in checks
                ],
            )

            finished = datetime.now(timezone.utc).replace(tzinfo=None)
            duration_ms = int((finished - started).total_seconds() * 1000)
            passed = sum(1 for check in checks if check.passed)
            failed = len(checks) - passed
            con.execute(
                "INSERT INTO meta_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    workspace,
                    started,
                    finished,
                    duration_ms,
                    "succeeded",
                    ods_rows,
                    len(steps),
                    passed,
                    failed,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    return RunResult(
        run_id=run_id,
        workspace_id=workspace_id,
        started_at=started,
        finished_at=finished,
        duration_ms=duration_ms,
        status="succeeded",
        ods_rows=ods_rows,
        tables_built=len(steps),
        checks_passed=passed,
        checks_failed=failed,
    )


def elapsed_ms(started: float) -> int:
    """给路由层记墙钟耗时用；与 run 内部计时分开，避免把排队时间算进执行时间。"""
    return int((time.perf_counter() - started) * 1000)
