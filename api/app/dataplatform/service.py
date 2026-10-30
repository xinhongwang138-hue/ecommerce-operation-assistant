"""中台只读服务层。

所有读操作都开 `read_only=True` 连接；仓文件不存在时返回"未就绪"而不是抛错，
因为"还没跑过批"是新装环境的正常状态，不该表现为 500。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb

from app.dataplatform import metrics as metric_registry
from app.dataplatform import warehouse
from app.dataplatform.pipeline import LAYER_LABELS, LAYERS, all_steps
from app.dataplatform.schemas import (
    LayerRead,
    LineageEdge,
    LineageNode,
    LineageRead,
    MetricPoint,
    MetricRead,
    MetricSeriesRead,
    OverviewRead,
    QualityCheckRead,
    QualityReportRead,
    RunRead,
    SummaryRead,
    TableRead,
)


def is_ready(workspace_id: int | None) -> bool:
    path = warehouse.warehouse_path(workspace_id)
    if not path.exists():
        return False
    try:
        with warehouse.connect(workspace_id, read_only=True) as con:
            count = con.execute("SELECT count(*) FROM meta_runs").fetchone()[0]
        return int(count) > 0
    except (duckdb.Error, FileNotFoundError):
        # 仓文件存在但没有 meta_runs（上一次跑批在事务里崩了）：视为未就绪，
        # 下一次跑批会重建，不在这里尝试修数据。
        return False


def _fetch(
    workspace_id: int | None, sql: str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    with warehouse.connect(workspace_id, read_only=True) as con:
        return con.execute(sql, params or []).fetchall()


def last_run(workspace_id: int | None) -> RunRead | None:
    if not is_ready(workspace_id):
        return None
    row = _fetch(
        workspace_id,
        """
        SELECT run_id, started_at, finished_at, duration_ms, status,
               ods_rows, tables_built, checks_passed, checks_failed
        FROM meta_runs ORDER BY finished_at DESC LIMIT 1
        """,
    )
    if not row:
        return None
    return RunRead(
        run_id=row[0][0],
        started_at=row[0][1],
        finished_at=row[0][2],
        duration_ms=int(row[0][3] or 0),
        status=row[0][4],
        ods_rows=int(row[0][5] or 0),
        tables_built=int(row[0][6] or 0),
        checks_passed=int(row[0][7] or 0),
        checks_failed=int(row[0][8] or 0),
    )


def list_runs(workspace_id: int | None, limit: int = 10) -> list[RunRead]:
    """最近若干次跑批。`limit` 夹在 1..50：这是个给人看的列表，不是导出接口。"""
    if not is_ready(workspace_id):
        return []
    rows = _fetch(
        workspace_id,
        """
        SELECT run_id, started_at, finished_at, duration_ms, status,
               ods_rows, tables_built, checks_passed, checks_failed
        FROM meta_runs ORDER BY finished_at DESC LIMIT ?
        """,
        [max(1, min(int(limit), 50))],
    )
    return [
        RunRead(
            run_id=row[0],
            started_at=row[1],
            finished_at=row[2],
            duration_ms=int(row[3] or 0),
            status=row[4],
            ods_rows=int(row[5] or 0),
            tables_built=int(row[6] or 0),
            checks_passed=int(row[7] or 0),
            checks_failed=int(row[8] or 0),
        )
        for row in rows
    ]


def _layer_rows(workspace_id: int | None, ready: bool) -> list[LayerRead]:
    counts: dict[str, tuple[str, int]] = {}
    if ready:
        for name, description, row_count in _fetch(
            workspace_id, "SELECT table_name, description, row_count FROM meta_tables"
        ):
            counts[name] = (description, int(row_count or 0))

    layers: list[LayerRead] = []
    for layer in LAYERS:
        tables = [
            TableRead(
                name=step.target,
                layer=layer,
                description=counts.get(step.target, (step.description, 0))[0],
                row_count=counts.get(step.target, (step.description, 0))[1],
            )
            for step in all_steps()
            if step.layer == layer
        ]
        layers.append(LayerRead(layer=layer, label=LAYER_LABELS[layer], tables=tables))
    return layers


def _summary(workspace_id: int | None) -> SummaryRead | None:
    row = _fetch(
        workspace_id,
        """
        SELECT window_start, window_end, gmv_7d, spend_7d, roas_7d, ctr_7d, cvr_7d,
               aov_7d, low_stock_ratio, agent_success_rate, asset_count,
               product_count, store_count
        FROM ads_ops_summary
        """,
    )
    if not row:
        return None
    values = row[0]
    return SummaryRead(
        window_start=values[0],
        window_end=values[1],
        gmv_7d=float(values[2] or 0.0),
        spend_7d=float(values[3] or 0.0),
        roas_7d=None if values[4] is None else float(values[4]),
        ctr_7d=None if values[5] is None else float(values[5]),
        cvr_7d=None if values[6] is None else float(values[6]),
        aov_7d=None if values[7] is None else float(values[7]),
        low_stock_ratio=None if values[8] is None else float(values[8]),
        agent_success_rate=None if values[9] is None else float(values[9]),
        asset_count=int(values[10] or 0),
        product_count=int(values[11] or 0),
        store_count=int(values[12] or 0),
    )


def overview(workspace_id: int | None) -> OverviewRead:
    ready = is_ready(workspace_id)
    run = last_run(workspace_id)
    summary = _summary(workspace_id) if ready else None
    return OverviewRead(
        ready=ready,
        run=run,
        layers=_layer_rows(workspace_id, ready),
        summary=summary,
        metric_count=len(metric_registry.METRICS),
        quality_passed=run.checks_passed if run else 0,
        quality_failed=run.checks_failed if run else 0,
    )


def list_tables(workspace_id: int | None) -> list[TableRead]:
    ready = is_ready(workspace_id)
    return [table for layer in _layer_rows(workspace_id, ready) for table in layer.tables]


def metric_catalog() -> list[MetricRead]:
    return [
        MetricRead(
            key=metric.key,
            name=metric.name,
            unit=metric.unit,
            direction=metric.direction,
            layer=metric.layer,
            source_table=metric.source_table,
            definition=metric.definition,
            formula=metric.formula,
        )
        for metric in metric_registry.METRICS.values()
    ]


def metric_series(workspace_id: int | None, key: str, days: int) -> MetricSeriesRead | None:
    metric = metric_registry.get_metric(key)
    if metric is None:
        return None
    payload = MetricRead(
        key=metric.key,
        name=metric.name,
        unit=metric.unit,
        direction=metric.direction,
        layer=metric.layer,
        source_table=metric.source_table,
        definition=metric.definition,
        formula=metric.formula,
    )
    if not is_ready(workspace_id):
        return MetricSeriesRead(metric=payload, points=[], has_data=False)

    try:
        rows = _fetch(workspace_id, metric_registry.resolve_sql(metric, days))
    except duckdb.Error:
        # 表还没建出来（例如刚换完口径）时返回空序列，让前端显示"暂无数据"，
        # 而不是把一个 500 甩给正在看板上的运营。
        return MetricSeriesRead(metric=payload, points=[], has_data=False)

    points = [
        MetricPoint(
            period=row[0] if isinstance(row[0], date) else None,
            value=None if row[1] is None else float(row[1]),
        )
        for row in rows
    ]
    return MetricSeriesRead(metric=payload, points=points, has_data=any(p.value is not None for p in points))


def lineage(_workspace_id: int | None) -> LineageRead:
    """血缘来自建表声明，不来自解析 SQL。

    解析器读不懂 `CREATE OR REPLACE ... WITH ...` 里真正用了哪几张表，而建表代码
    自己知道 depends_on；声明式的代价是必须人工维护，所以把它钉在 Step 定义上，
    新增表时"不写 depends_on"会立刻在图上表现为孤岛。
    """
    steps = all_steps()
    nodes: dict[str, str] = {}
    edges: list[LineageEdge] = []
    upstream: dict[str, list[str]] = {}
    for step in steps:
        nodes.setdefault(step.target, step.layer)
        for source in step.depends_on:
            nodes.setdefault(source, "SRC")
            edges.append(LineageEdge(source=source, target=step.target, step=step.name))
            upstream.setdefault(step.target, []).append(source)
    ordered = sorted(nodes.items(), key=lambda item: (_LAYER_ORDER.get(item[1], 9), item[0]))
    return LineageRead(
        nodes=[LineageNode(name=name, layer=layer) for name, layer in ordered],
        edges=edges,
        upstream=upstream,
    )


_LAYER_ORDER = {"SRC": 0, "ODS": 1, "DWD": 2, "DWS": 3, "ADS": 4}


def quality_report(workspace_id: int | None) -> QualityReportRead:
    run = last_run(workspace_id)
    if run is None:
        return QualityReportRead(run_id=None, checked_at=None, passed=0, failed=0, checks=[])
    rows = _fetch(
        workspace_id,
        """
        SELECT check_key, name, layer, table_name, severity, observed, passed, expectation
        FROM meta_quality WHERE run_id = ?
        ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, check_key
        """,
        [run.run_id],
    )
    checks = [
        QualityCheckRead(
            key=row[0],
            name=row[1],
            layer=row[2],
            table=row[3],
            severity=row[4],
            observed=int(row[5] or 0),
            passed=bool(row[6]),
            expectation=row[7],
        )
        for row in rows
    ]
    return QualityReportRead(
        run_id=run.run_id,
        checked_at=run.finished_at,
        passed=sum(1 for check in checks if check.passed),
        failed=sum(1 for check in checks if not check.passed),
        checks=checks,
    )
