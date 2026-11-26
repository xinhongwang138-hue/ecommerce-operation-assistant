"""ODS 抽取规格。

为什么把 SQL 写成显式字符串而不是复用 ORM 模型：ODS 的职责是"当时长什么样就存什么样"，
列一旦跟着模型走，后续给 Product 加字段就会让历史跑批结果无法复现。这里冻结字段清单，
模型加列不会悄悄改变 ODS 的宽度。

抽取一律按 Workspace 过滤（`:ws` 为 None 时表示库内还没有 Workspace 的兼容场景）。
子表没有 workspace_id，必须回连 products 判定归属——直接按 id 取会跨租户。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

EXTRACTED_AT_COLUMN = "_extracted_at"
EXTRACTED_AT_TYPE = "TIMESTAMP"


@dataclass(frozen=True)
class Column:
    name: str
    duckdb_type: str


@dataclass(frozen=True)
class SourceSpec:
    """一条 ODS 抽取：源表 → 仓表，字段与类型全部显式声明。"""

    target: str
    source_table: str
    description: str
    columns: tuple[Column, ...]
    sql: str


_WORKSPACE_FILTER = "(:ws IS NULL OR {alias}.workspace_id = :ws)"

SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        target="ods_stores",
        source_table="stores",
        description="店铺维表原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("workspace_id", "BIGINT"),
            Column("name", "VARCHAR"),
            Column("platform", "VARCHAR"),
            Column("status", "VARCHAR"),
            Column("created_at", "TIMESTAMP"),
        ),
        sql=f"""
            SELECT id, workspace_id, name, platform, status, created_at
            FROM stores s
            WHERE {_WORKSPACE_FILTER.format(alias='s')}
        """,
    ),
    SourceSpec(
        target="ods_products",
        source_table="products",
        description="商品维表原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("workspace_id", "BIGINT"),
            Column("store_id", "BIGINT"),
            Column("name", "VARCHAR"),
            Column("platform", "VARCHAR"),
            Column("category", "VARCHAR"),
            Column("price", "DOUBLE"),
            Column("cost", "DOUBLE"),
            Column("status", "VARCHAR"),
        ),
        sql=f"""
            SELECT id, workspace_id, store_id, name, platform, category,
                   price, cost, platform_status AS status
            FROM products p
            WHERE {_WORKSPACE_FILTER.format(alias='p')}
        """,
    ),
    SourceSpec(
        target="ods_skus",
        source_table="product_skus",
        description="SKU 维表原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("product_id", "BIGINT"),
            Column("sku_code", "VARCHAR"),
            Column("name", "VARCHAR"),
            Column("price", "DOUBLE"),
            Column("cost", "DOUBLE"),
            Column("status", "VARCHAR"),
        ),
        sql=f"""
            SELECT k.id, k.product_id, k.sku_code, k.name, k.price, k.cost, k.status
            FROM product_skus k
            JOIN products p ON p.id = k.product_id
            WHERE {_WORKSPACE_FILTER.format(alias='p')}
        """,
    ),
    SourceSpec(
        target="ods_inventory",
        source_table="inventory_items",
        description="库存快照原样落地",
        columns=(
            Column("sku_id", "BIGINT"),
            Column("quantity", "BIGINT"),
            Column("locked_quantity", "BIGINT"),
            Column("warning_threshold", "BIGINT"),
        ),
        sql=f"""
            SELECT i.sku_id, i.quantity, i.locked_quantity, i.warning_threshold
            FROM inventory_items i
            JOIN product_skus k ON k.id = i.sku_id
            JOIN products p ON p.id = k.product_id
            WHERE {_WORKSPACE_FILTER.format(alias='p')}
        """,
    ),
    SourceSpec(
        target="ods_performance",
        source_table="performance_records",
        description="经营数据明细原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("product_id", "BIGINT"),
            Column("period_start", "DATE"),
            Column("period_end", "DATE"),
            Column("impressions", "BIGINT"),
            Column("clicks", "BIGINT"),
            Column("conversions", "BIGINT"),
            Column("spend", "DOUBLE"),
            Column("revenue", "DOUBLE"),
        ),
        sql=f"""
            SELECT r.id, r.product_id, r.period_start, r.period_end,
                   r.impressions, r.clicks, r.conversions, r.spend, r.revenue
            FROM performance_records r
            JOIN products p ON p.id = r.product_id
            WHERE {_WORKSPACE_FILTER.format(alias='p')}
        """,
    ),
    SourceSpec(
        target="ods_creative_plans",
        source_table="creative_plans",
        description="创意方案原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("product_id", "BIGINT"),
            Column("type", "VARCHAR"),
            Column("status", "VARCHAR"),
            Column("created_at", "TIMESTAMP"),
        ),
        sql=f"""
            SELECT c.id, c.product_id, c.type, c.status, c.created_at
            FROM creative_plans c
            JOIN products p ON p.id = c.product_id
            WHERE {_WORKSPACE_FILTER.format(alias='p')}
        """,
    ),
    SourceSpec(
        target="ods_generated_assets",
        source_table="generated_assets",
        description="素材库原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("product_id", "BIGINT"),
            Column("creative_plan_id", "BIGINT"),
            Column("asset_type", "VARCHAR"),
            Column("status", "VARCHAR"),
            Column("rating", "BIGINT"),
            Column("created_at", "TIMESTAMP"),
        ),
        sql=f"""
            SELECT a.id, a.product_id, a.creative_plan_id, a.asset_type,
                   a.status, a.rating, a.created_at
            FROM generated_assets a
            JOIN products p ON p.id = a.product_id
            WHERE {_WORKSPACE_FILTER.format(alias='p')}
        """,
    ),
    SourceSpec(
        target="ods_agent_tasks",
        source_table="agent_tasks",
        description="Agent 任务原样落地",
        columns=(
            Column("id", "BIGINT"),
            Column("workspace_id", "BIGINT"),
            Column("workflow_key", "VARCHAR"),
            Column("status", "VARCHAR"),
            Column("risk_level", "VARCHAR"),
            Column("created_at", "TIMESTAMP"),
            Column("finished_at", "TIMESTAMP"),
        ),
        sql=f"""
            SELECT t.id, t.workspace_id, t.workflow_key, t.status,
                   t.risk_level, t.created_at, t.finished_at
            FROM agent_tasks t
            WHERE {_WORKSPACE_FILTER.format(alias='t')}
        """,
    ),
)


def _coerce(value: Any, duckdb_type: str) -> Any:
    """Oracle/MySQL 驱动返回的类型比 DuckDB 宽，逐列收敛一次。"""
    if value is None:
        return None
    if duckdb_type == "DOUBLE":
        return float(value) if isinstance(value, (Decimal, int, float)) else value
    if duckdb_type == "BIGINT":
        return int(value) if isinstance(value, (Decimal, int, float)) else value
    if duckdb_type == "VARCHAR":
        return str(value)
    if duckdb_type in {"DATE", "TIMESTAMP"} and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def extract_rows(
    db: Session,
    spec: SourceSpec,
    *,
    workspace_id: int | None,
    extracted_at: datetime,
) -> list[tuple[Any, ...]]:
    """按规格抽取一批行，并统一补提取时间戳。"""
    rows = db.execute(text(spec.sql), {"ws": workspace_id}).all()
    columns = spec.columns
    payload: list[tuple[Any, ...]] = []
    for row in rows:
        payload.append(
            tuple(_coerce(value, column.duckdb_type) for value, column in zip(row, columns))
            + (extracted_at,)
        )
    return payload


def extract_all(
    db: Session,
    *,
    workspace_id: int | None,
    extracted_at: datetime,
) -> dict[str, int]:
    """抽取全部源表，返回 {ODS 表: 行数}。"""
    counts: dict[str, int] = {}
    for spec in SOURCES:
        counts[spec.target] = len(
            extract_rows(db, spec, workspace_id=workspace_id, extracted_at=extracted_at)
        )
    return counts