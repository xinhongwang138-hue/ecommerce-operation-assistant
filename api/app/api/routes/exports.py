import csv
import io
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.auth import require_view_permission
from app.db.session import get_db
from app.models import AgentRun, PerformanceRecord, Product, User

router = APIRouter(prefix="/exports", tags=["exports"])
SessionDep = Annotated[Session, Depends(get_db)]
ViewerDep = Annotated[User, Depends(require_view_permission)]


def _csv_response(header: list[str], rows: list[list], filename: str) -> Response:
    """把行数据编码为带 BOM 的 CSV，Excel 双击打开中文不乱码。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    content = buffer.getvalue().encode("utf-8-sig")
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}-{stamp}.csv"'},
    )


@router.get("/products.csv")
def export_products(db: SessionDep, _current_user: ViewerDep) -> Response:
    """导出商品清单（只读，viewer 及以上）。"""
    products = db.scalars(select(Product).order_by(Product.id.asc())).all()
    header = [
        "ID",
        "商品名",
        "平台",
        "类目",
        "售价",
        "成本",
        "目标人群",
        "卖点",
        "店铺ID",
        "平台商品ID",
        "平台状态",
        "创建时间",
    ]
    rows = [
        [
            product.id,
            product.name,
            product.platform,
            product.category or "",
            product.price or "",
            product.cost or "",
            product.target_audience or "",
            " / ".join(product.selling_points or []),
            product.store_id or "",
            product.external_product_id or "",
            product.platform_status or "",
            product.created_at.isoformat() if product.created_at else "",
        ]
        for product in products
    ]
    return _csv_response(header, rows, "products")


@router.get("/performance-records.csv")
def export_performance_records(db: SessionDep, _current_user: ViewerDep) -> Response:
    """导出全部绩效记录，附带商品名便于直接做透视。"""
    records = db.execute(
        select(PerformanceRecord, Product.name)
        .join(Product, Product.id == PerformanceRecord.product_id)
        .order_by(PerformanceRecord.period_start.desc())
    ).all()
    header = [
        "记录ID",
        "商品ID",
        "商品名",
        "开始日期",
        "结束日期",
        "曝光",
        "点击",
        "CTR",
        "转化",
        "转化率",
        "花费",
        "收入",
        "ROI",
        "备注",
        "创建时间",
    ]
    rows = [
        [
            record.id,
            record.product_id,
            product_name,
            record.period_start.isoformat() if record.period_start else "",
            record.period_end.isoformat() if record.period_end else "",
            record.impressions if record.impressions is not None else "",
            record.clicks if record.clicks is not None else "",
            record.ctr or "",
            record.conversions if record.conversions is not None else "",
            record.conversion_rate or "",
            record.spend or "",
            record.revenue or "",
            record.roi or "",
            record.notes or "",
            record.created_at.isoformat() if record.created_at else "",
        ]
        for record, product_name in records
    ]
    return _csv_response(header, rows, "performance-records")


@router.get("/usage.csv")
def export_usage(db: SessionDep, _current_user: ViewerDep) -> Response:
    """导出每次 Agent 运行的 token 用量明细。"""
    runs = db.scalars(select(AgentRun).order_by(AgentRun.created_at.desc())).all()
    header = [
        "运行ID",
        "商品ID",
        "工作流",
        "状态",
        "输入Token",
        "输出Token",
        "总Token",
        "调用次数",
        "创建时间",
        "完成时间",
    ]
    rows = []
    for run in runs:
        usage = run.usage or {}
        rows.append(
            [
                run.id,
                run.product_id or "",
                run.workflow_key,
                run.status,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("total_tokens", 0),
                usage.get("calls", 0),
                run.created_at.isoformat() if run.created_at else "",
                run.finished_at.isoformat() if run.finished_at else "",
            ]
        )
    return _csv_response(header, rows, "usage")
