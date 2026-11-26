"""数据中台的对外 DTO。

刻意不放进 `app/schemas.py`：那个文件是业务接口的公共契约，改一次要牵动
全平台；中台的响应形状只在 `/data-platform` 这一条链路上使用，放模块内
可以把"分析侧接口演进"与"业务接口兼容"解耦。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class RunRead(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status: str
    ods_rows: int
    tables_built: int
    checks_passed: int
    checks_failed: int


class TableRead(BaseModel):
    name: str
    layer: str
    description: str
    row_count: int


class LayerRead(BaseModel):
    layer: str
    label: str
    tables: list[TableRead]


class SummaryRead(BaseModel):
    window_start: date | None
    window_end: date | None
    gmv_7d: float
    spend_7d: float
    roas_7d: float | None
    ctr_7d: float | None
    cvr_7d: float | None
    aov_7d: float | None
    low_stock_ratio: float | None
    agent_success_rate: float | None
    asset_count: int
    product_count: int
    store_count: int


class OverviewRead(BaseModel):
    ready: bool
    run: RunRead | None
    layers: list[LayerRead]
    summary: SummaryRead | None
    metric_count: int
    quality_passed: int
    quality_failed: int


class MetricRead(BaseModel):
    key: str
    name: str
    unit: str
    direction: str
    layer: str
    source_table: str
    definition: str
    formula: str


class MetricPoint(BaseModel):
    period: date | None
    value: float | None


class MetricSeriesRead(BaseModel):
    metric: MetricRead
    points: list[MetricPoint]
    has_data: bool


class LineageNode(BaseModel):
    name: str
    layer: str


class LineageEdge(BaseModel):
    source: str
    target: str
    step: str


class LineageRead(BaseModel):
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    upstream: dict[str, list[str]] = Field(default_factory=dict)


class QualityCheckRead(BaseModel):
    key: str
    name: str
    layer: str
    table: str
    severity: str
    observed: int
    passed: bool
    expectation: str


class QualityReportRead(BaseModel):
    run_id: str | None
    checked_at: datetime | None
    passed: int
    failed: int
    checks: list[QualityCheckRead]


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=4000)


class QueryResultRead(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    max_rows: int
    elapsed_ms: int


class RunStartedRead(BaseModel):
    run: RunRead
    elapsed_ms: int
