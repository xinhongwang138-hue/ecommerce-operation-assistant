"""数据中台跑批与建模的测试。

这些断言都对准"会静默出错"的地方：重复导入是否去重、比率是否先聚合再相除、
窗口右端取的是业务日期还是系统日期、重跑会不会翻倍、租户之间会不会串仓。
纯 UI 或纯映射层的细节不在这里测——它们出错会立刻看见。
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy.orm import Session

from app.dataplatform import service
from app.dataplatform.metrics import METRICS, resolve_sql
from app.dataplatform.pipeline import LAYERS, all_steps, run_pipeline
from app.dataplatform.warehouse import connect
from app.models import Base, InventoryItem
from app.models import ProductSku as SkuModel
from tests.dataplatform_support import add_performance, seed_tenant
from tests.support import make_engine


@pytest.fixture()
def db():
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def warehouse_dir(tmp_path, monkeypatch):
    target = tmp_path / "warehouse"
    monkeypatch.setenv("WAREHOUSE_DIR", str(target))
    return target


def scalar(workspace_id: int, sql: str):
    with connect(workspace_id, read_only=True) as con:
        return con.execute(sql).fetchone()[0]


def rows(workspace_id: int, sql: str):
    with connect(workspace_id, read_only=True) as con:
        return con.execute(sql).fetchall()


def test_pipeline_builds_four_layers(db):
    tenant = seed_tenant(db)
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=1000, clicks=100, conversions=10, spend=100, revenue=500
    )

    result = run_pipeline(db, tenant.workspace_id)

    assert result.status == "succeeded"
    assert result.tables_built == len(all_steps())
    assert result.checks_failed == 0

    overview = service.overview(tenant.workspace_id)
    assert overview.ready is True
    assert [layer.layer for layer in overview.layers] == list(LAYERS)
    counts = {table.name: table.row_count for layer in overview.layers for table in layer.tables}
    assert counts["ods_performance"] == 1
    assert counts["dwd_perf_fact"] == 1
    assert counts["dws_store_daily"] == 1
    assert counts["ads_ops_summary"] == 1


def test_duplicate_performance_keeps_latest_row(db):
    tenant = seed_tenant(db)
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=100, clicks=10, conversions=1, spend=10, revenue=50
    )
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=200, clicks=20, conversions=2, spend=20, revenue=90
    )

    run_pipeline(db, tenant.workspace_id)

    assert scalar(tenant.workspace_id, "SELECT count(*) FROM ods_performance") == 2
    assert scalar(tenant.workspace_id, "SELECT count(*) FROM dwd_perf_fact") == 1
    assert scalar(tenant.workspace_id, "SELECT revenue FROM dwd_perf_fact") == pytest.approx(90.0)


def test_summary_aggregates_before_dividing(db):
    tenant = seed_tenant(db)
    # 两天数据刻意不对称：先按行求 ROAS 再平均会得到 (50 + 2) / 2 = 26，
    # 而正确口径是 (500+2000)/(10+1000) ≈ 2.475。差距足够大，口径写错就一定会被发现。
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=100, clicks=10, conversions=2, spend=10, revenue=500
    )
    add_performance(
        db, tenant.product, day=date(2026, 9, 2), impressions=10000, clicks=1000, conversions=100, spend=1000, revenue=2000
    )

    run_pipeline(db, tenant.workspace_id)
    summary = service.overview(tenant.workspace_id).summary
    assert summary is not None

    assert summary.gmv_7d == pytest.approx(2500.0)
    assert summary.spend_7d == pytest.approx(1010.0)
    # 按行求比值再平均会得到 (50 + 2) / 2 = 26；先聚合是 2500/1010 ≈ 2.475
    assert summary.roas_7d == pytest.approx(2500.0 / 1010.0)
    assert summary.ctr_7d == pytest.approx(1010.0 / 10100.0)
    assert summary.cvr_7d == pytest.approx(102.0 / 1010.0)
    assert summary.aov_7d == pytest.approx(2500.0 / 102.0)


def test_window_ends_at_latest_business_date(db):
    """补录历史数据时，右端必须跟着数据走，不能是 CURRENT_DATE。"""
    tenant = seed_tenant(db)
    add_performance(
        db, tenant.product, day=date(2026, 1, 5), impressions=100, clicks=10, conversions=1, spend=10, revenue=50
    )

    run_pipeline(db, tenant.workspace_id)
    summary = service.overview(tenant.workspace_id).summary

    assert summary is not None
    assert summary.window_end == date(2026, 1, 5)
    assert summary.window_start == date(2025, 12, 30)
    assert summary.gmv_7d == pytest.approx(50.0)


def test_rerun_is_idempotent(db):
    tenant = seed_tenant(db)
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=100, clicks=10, conversions=1, spend=10, revenue=50
    )

    run_pipeline(db, tenant.workspace_id)
    run_pipeline(db, tenant.workspace_id)

    assert scalar(tenant.workspace_id, "SELECT count(*) FROM ods_performance") == 1
    assert scalar(tenant.workspace_id, "SELECT count(*) FROM dwd_perf_fact") == 1
    assert scalar(tenant.workspace_id, "SELECT count(*) FROM meta_runs") == 2


def test_tenants_get_separate_warehouse_files(db, warehouse_dir):
    first = seed_tenant(db, "alpha")
    second = seed_tenant(db, "beta")
    add_performance(
        db, first.product, day=date(2026, 9, 1), impressions=100, clicks=10, conversions=1, spend=10, revenue=50
    )
    add_performance(
        db, second.product, day=date(2026, 9, 1), impressions=100, clicks=10, conversions=1, spend=10, revenue=999
    )

    run_pipeline(db, first.workspace_id)
    run_pipeline(db, second.workspace_id)

    assert (warehouse_dir / f"workspace_{first.workspace_id}.duckdb").exists()
    assert (warehouse_dir / f"workspace_{second.workspace_id}.duckdb").exists()

    names = [row[0] for row in rows(first.workspace_id, "SELECT product_name FROM dwd_product_dim")]
    assert names == ["alpha 洗发水"]
    assert scalar(second.workspace_id, "SELECT gmv_7d FROM ads_ops_summary") == pytest.approx(999.0)


def test_low_stock_ratio_counts_oversold_as_low_stock(db):
    tenant = seed_tenant(db)
    sku = db.get(SkuModel, tenant.sku.id)
    assert sku is not None
    item = db.query(InventoryItem).filter(InventoryItem.sku_id == sku.id).one()
    item.quantity = 3
    item.locked_quantity = 5
    db.commit()

    run_pipeline(db, tenant.workspace_id)
    summary = service.overview(tenant.workspace_id).summary

    assert summary is not None
    assert summary.low_stock_ratio == pytest.approx(1.0)
    assert scalar(tenant.workspace_id, "SELECT available_qty FROM dwd_sku_dim") == -2


def test_quality_checks_catch_bad_rows(db):
    tenant = seed_tenant(db)
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=10, clicks=20, conversions=1, spend=-5, revenue=50
    )

    result = run_pipeline(db, tenant.workspace_id)
    report = service.quality_report(tenant.workspace_id)

    assert result.checks_failed >= 2
    failed = {check.key: check for check in report.checks if not check.passed}
    assert failed["perf_spend_non_negative"].observed == 1
    assert failed["perf_clicks_within_impressions"].observed == 1
    assert failed["perf_spend_non_negative"].severity == "critical"


def test_every_metric_is_executable_after_run(db):
    tenant = seed_tenant(db)
    add_performance(
        db, tenant.product, day=date(2026, 9, 1), impressions=100, clicks=10, conversions=1, spend=10, revenue=50
    )
    run_pipeline(db, tenant.workspace_id)

    for key, metric in METRICS.items():
        series = service.metric_series(tenant.workspace_id, key, 14)
        assert series is not None, key
        assert series.metric.definition.strip(), f"{key} 缺少口径说明"
        assert series.metric.formula.strip(), f"{key} 缺少计算公式"
        # 可执行：能取到行且没有异常；无数据的指标也允许返回空序列
        assert resolve_sql(metric, 14)

    assert service.metric_series(tenant.workspace_id, "gmv", 14).has_data is True
    assert service.metric_series(tenant.workspace_id, "missing", 14) is None


def test_lineage_covers_every_table_and_links_layers(db):
    lineage = service.lineage(1)
    built = {step.target for step in all_steps()}
    nodes = {node.name for node in lineage.nodes}

    assert built.issubset(nodes)
    assert lineage.upstream["dwd_perf_fact"] == ["ods_performance", "dwd_product_dim"]
    assert "ads_ops_daily" in lineage.upstream["ads_ops_summary"]
    assert any(edge.step == "build_ops_summary" for edge in lineage.edges)


def test_overview_before_first_run_is_not_ready(db):
    tenant = seed_tenant(db)

    overview = service.overview(tenant.workspace_id)

    assert overview.ready is False
    assert overview.run is None
    assert overview.summary is None
    assert len(overview.layers) == len(LAYERS)
    assert all(table.row_count == 0 for layer in overview.layers for table in layer.tables)


def test_store_count_counts_dimension_not_kpi_rows(db):
    """看板上的"店铺 N 个"必须数在册店铺。

    回归点：store_count 曾经取 `count(*) from ads_store_kpi`，而那张表只装有投放
    数据的店铺。一个刚建好、有 1 家店但还没录经营数据的租户会在看板上显示
    "商品 1 个 · 店铺 0 个"——数字没错，但读起来是错的。
    """
    tenant = seed_tenant(db)

    run_pipeline(db, tenant.workspace_id)
    summary = service.overview(tenant.workspace_id).summary

    assert summary.store_count == 1
    assert summary.product_count == 1
    # 没有经营数据时，店铺 KPI 表本来就是空的，两件事不能混为一谈
    assert service.list_tables(tenant.workspace_id)  # 表清单仍可读
    assert summary.gmv_7d == 0.0
