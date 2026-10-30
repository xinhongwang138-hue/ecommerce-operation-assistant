"""数据中台（P06）接口。

权限切分：读（概览/表/指标/血缘/质量）走 view，跑批与自由查询走 edit。
自由查询虽然是只读的，但它能让任何登录用户把整仓扫一遍，属于"能影响别人
体验"的操作，因此不放开给 viewer。
"""

from __future__ import annotations

import time
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.dataplatform import service, warehouse
from app.dataplatform.pipeline import run_pipeline
from app.dataplatform.schemas import (
    LineageRead,
    MetricRead,
    MetricSeriesRead,
    OverviewRead,
    QualityReportRead,
    QueryRequest,
    QueryResultRead,
    RunRead,
    RunStartedRead,
    TableRead,
)
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/data-platform", tags=["data-platform"])

SessionDep = Annotated[Session, Depends(get_db)]
ViewUserDep = Annotated[User, Depends(require_view_permission)]
EditUserDep = Annotated[User, Depends(require_edit_permission)]


@router.get("/overview", response_model=OverviewRead)
def read_overview(db: SessionDep, _user: ViewUserDep) -> OverviewRead:
    """概览：最近一次跑批、四层表清单与行数、全站近 7 天指标、质量通过数。"""
    return service.overview(workspace_scope(db))


@router.get("/tables", response_model=list[TableRead])
def read_tables(db: SessionDep, _user: ViewUserDep) -> list[TableRead]:
    """四层全部表及其行数；未跑批时返回计划中的表（行数 0）。"""
    return service.list_tables(workspace_scope(db))


@router.get("/metrics", response_model=list[MetricRead])
def read_metrics(_user: ViewUserDep) -> list[MetricRead]:
    """指标口径目录：不依赖仓是否就绪，口径是代码里的声明。"""
    return service.metric_catalog()


@router.get("/metrics/{key}", response_model=MetricSeriesRead)
def read_metric_series(
    key: str,
    db: SessionDep,
    _user: ViewUserDep,
    days: int = 14,
) -> MetricSeriesRead:
    series = service.metric_series(workspace_scope(db), key, days)
    if series is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Metric not found")
    return series


@router.get("/lineage", response_model=LineageRead)
def read_lineage(db: SessionDep, _user: ViewUserDep) -> LineageRead:
    """表级血缘：节点按层排序，边来自建表声明，可直接反查上游。"""
    return service.lineage(workspace_scope(db))


@router.get("/quality", response_model=QualityReportRead)
def read_quality(db: SessionDep, _user: ViewUserDep) -> QualityReportRead:
    """最近一次跑批的质量校验结果，critical 排在前面。"""
    return service.quality_report(workspace_scope(db))


@router.get("/runs", response_model=list[RunRead])
def read_runs(db: SessionDep, _user: ViewUserDep) -> list[RunRead]:
    """最近 10 次跑批记录。"""
    return service.list_runs(workspace_scope(db))


@router.post("/runs", response_model=RunStartedRead, status_code=status.HTTP_201_CREATED)
def trigger_run(db: SessionDep, _user: EditUserDep) -> RunStartedRead:
    """全量重建一次仓。

    同步执行而不是丢 Celery：数据量在十万行内，一次跑批是秒级；异步化只会让
    "点完按钮不知道好没好"变成新的问题。真到分钟级再换队列，那时候的瓶颈
    是数据量，不是接口形态。
    """
    started = time.perf_counter()
    result = run_pipeline(db, workspace_scope(db))
    return RunStartedRead(
        run=RunRead(**result.as_dict()),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


@router.post("/query", response_model=QueryResultRead)
def run_query(payload: QueryRequest, db: SessionDep, _user: EditUserDep) -> QueryResultRead:
    """护栏内的只读查询：单条 SELECT/WITH、表白名单、强制行数上限。"""
    workspace_id = workspace_scope(db)
    if not service.is_ready(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据仓尚未构建，请先执行一次抽取",
        )

    started = time.perf_counter()
    try:
        result = warehouse.execute_guarded(workspace_id, payload.sql)
    except warehouse.QueryRejected as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据仓文件不存在，请先执行一次抽取",
        ) from exc
    except duckdb.Error as exc:
        # SQL 语法/字段错误是用户输入问题，回 400 并把原始信息带回去，
        # 否则用户只能看到一个没有上下文的 500。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SQL 执行失败：{exc}",
        ) from exc

    return QueryResultRead(
        **result,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
