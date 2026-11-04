"""只读查询护栏的测试。

护栏是这个模块唯一"对外开放自由 SQL"的地方，因此每条规则都要有一条测试，
包括"看起来无害但其实能读文件"的函数。这里不测 SQL 语义，只测边界。
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import duckdb
import pytest
from sqlalchemy.orm import Session

from app.dataplatform.pipeline import run_pipeline
from app.dataplatform.warehouse import QueryRejected, connect, execute_guarded, guard_select
from app.models import Base
from tests.dataplatform_support import add_performance, seed_tenant
from tests.support import make_engine


@pytest.fixture()
def db():
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def tenant(db, tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path / "warehouse"))
    seeded = seed_tenant(db)
    for day in (1, 2, 3):
        add_performance(
            db,
            seeded.product,
            day=date(2026, 9, day),
            impressions=100,
            clicks=10,
            conversions=1,
            spend=10,
            revenue=50,
        )
    run_pipeline(db, seeded.workspace_id)
    return seeded


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM ads_ops_daily", "只允许 SELECT"),
        ("UPDATE ads_ops_daily SET gmv = 0", "只允许 SELECT"),
        ("DROP TABLE dwd_perf_fact", "只允许 SELECT"),
        ("SELECT 1; SELECT 2", "单条语句"),
        ("SELECT * FROM products", "白名单"),
        ("SELECT * FROM other.ads_ops_daily", "跨库"),
        ("SELECT * FROM read_csv('C:/secret.csv')", "禁用"),
        ("ATTACH 'C:/other.duckdb' AS other", "禁用"),
        ("SELECT * FROM duckdb_settings()", "禁用"),
        ("", "不能为空"),
    ],
)
def test_guard_rejects(sql, reason):
    with pytest.raises(QueryRejected) as caught:
        guard_select(sql)
    assert reason in str(caught.value)


def test_guard_allows_cte_and_comments():
    guarded, limit = guard_select(
        """
        -- 运营手动查的 GMV 趋势
        WITH daily AS (SELECT date, gmv FROM ads_ops_daily)
        SELECT date, gmv FROM daily ORDER BY date
        """
    )
    assert "daily" in guarded
    assert f"LIMIT {limit + 1}" in guarded


def test_guard_accepts_plain_select_without_from():
    guarded, _ = guard_select("SELECT 1 AS ok")
    assert "SELECT 1 AS ok" in guarded


def test_execute_guarded_returns_columns_and_truncates(tenant):
    result = execute_guarded(
        tenant.workspace_id,
        "SELECT date, gmv FROM ads_ops_daily ORDER BY date",
        max_rows=2,
    )

    assert result["columns"] == ["date", "gmv"]
    assert result["row_count"] == 2
    assert result["truncated"] is True
    assert result["max_rows"] == 2


def test_execute_guarded_does_not_truncate_when_under_limit(tenant):
    result = execute_guarded(tenant.workspace_id, "SELECT count(*) AS n FROM ads_ops_daily")
    assert result["rows"] == [[3]]
    assert result["truncated"] is False


def test_read_only_connection_rejects_writes(tenant):
    with pytest.raises(duckdb.Error):
        with connect(tenant.workspace_id, read_only=True) as con:
            con.execute("CREATE TABLE hack AS SELECT 1")


def test_missing_warehouse_raises_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path / "empty"))
    with pytest.raises(FileNotFoundError):
        with connect(999, read_only=True) as con:
            con.execute("SELECT 1")
