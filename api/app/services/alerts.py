from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AgentApproval,
    AgentTask,
    Competitor,
    CompetitorMonitorSnapshot,
    InventoryItem,
    Product,
    ProductSku,
)

_ITEM_LIMIT = 20


def read_alert_summary(db: Session) -> dict:
    """汇总待办信号：低库存、竞品变化、Agent 任务失败、待审批。

    全部基于现有表实时计算，不新增存储；每类只返回前 20 条，count 为真实总数。
    """
    groups = [
        _low_stock_group(db),
        _competitor_change_group(db),
        _agent_failed_group(db),
        _approval_pending_group(db),
    ]
    return {"total": sum(group["count"] for group in groups), "groups": groups}


def _low_stock_group(db: Session) -> dict:
    # 与库存汇总口径保持一致：可用 = 现货 - 锁定，可用 <= 预警线即视为低库存。
    available = InventoryItem.quantity - InventoryItem.locked_quantity
    is_low = available <= InventoryItem.warning_threshold
    count = db.scalar(select(func.count()).select_from(InventoryItem).where(is_low)) or 0
    rows = db.execute(
        select(
            Product.id,
            Product.name,
            ProductSku.sku_code,
            available.label("available"),
            InventoryItem.warning_threshold,
        )
        .join(ProductSku, ProductSku.id == InventoryItem.sku_id)
        .join(Product, Product.id == ProductSku.product_id)
        .where(is_low)
        .order_by(available.asc())
        .limit(_ITEM_LIMIT)
    ).all()
    return {
        "key": "low_stock",
        "title": "低库存 SKU",
        "hint": "可用库存已低于或等于预警线，建议优先补货",
        "count": count,
        "items": [
            {
                "id": product_id,
                "title": product_name,
                "subtitle": f"{sku_code} 可用 {avail}（预警线 {threshold}）",
            }
            for product_id, product_name, sku_code, avail, threshold in rows
        ],
    }


def _competitor_change_group(db: Session) -> dict:
    # 快照按时间倒序取最近一批，同一监测项只认最新一条，避免历史变化被重复计数。
    rows = db.execute(
        select(
            CompetitorMonitorSnapshot.monitor_id,
            CompetitorMonitorSnapshot.product_id,
            CompetitorMonitorSnapshot.changes,
            Competitor.name,
        )
        .join(Competitor, Competitor.id == CompetitorMonitorSnapshot.competitor_id)
        .where(CompetitorMonitorSnapshot.status == "succeeded")
        .order_by(CompetitorMonitorSnapshot.checked_at.desc())
        .limit(300)
    ).all()

    seen: set[int] = set()
    latest: list[tuple[int, str | None, list]] = []
    for monitor_id, product_id, changes, competitor_name in rows:
        if monitor_id in seen:
            continue
        # 先标记已处理，保证“最新一条变化为空”的监测项不会被更早的历史变化顶上来。
        seen.add(monitor_id)
        if isinstance(changes, list) and changes:
            latest.append((product_id, competitor_name, changes))

    return {
        "key": "competitor_change",
        "title": "竞品变化",
        "hint": "最近一次巡检发现价格或卖点变化",
        "count": len(latest),
        "items": [
            {
                "id": product_id,
                "title": competitor_name or "未知竞品",
                "subtitle": "、".join(str(change) for change in changes[:3]),
            }
            for product_id, competitor_name, changes in latest[:_ITEM_LIMIT]
        ],
    }


def _agent_failed_group(db: Session) -> dict:
    count = db.scalar(
        select(func.count()).select_from(AgentTask).where(AgentTask.status == "failed")
    ) or 0
    rows = db.scalars(
        select(AgentTask)
        .where(AgentTask.status == "failed")
        .order_by(AgentTask.updated_at.desc())
        .limit(_ITEM_LIMIT)
    ).all()
    return {
        "key": "agent_failed",
        "title": "失败的 Agent 任务",
        "hint": "需要排查原因或重新发起",
        "count": count,
        "items": [
            {
                "id": task.id,
                "title": task.title,
                "subtitle": task.error or task.workflow_key,
            }
            for task in rows
        ],
    }


def _approval_pending_group(db: Session) -> dict:
    count = db.scalar(
        select(func.count()).select_from(AgentApproval).where(AgentApproval.status == "pending")
    ) or 0
    rows = db.scalars(
        select(AgentApproval)
        .where(AgentApproval.status == "pending")
        .order_by(AgentApproval.created_at.desc())
        .limit(_ITEM_LIMIT)
    ).all()
    return {
        "key": "approval_pending",
        "title": "待审批",
        "hint": "等待人工确认后才会继续执行",
        "count": count,
        "items": [
            {
                "id": approval.id,
                "title": approval.action_key,
                "subtitle": f"风险等级 {approval.risk_level}",
            }
            for approval in rows
        ],
    }
