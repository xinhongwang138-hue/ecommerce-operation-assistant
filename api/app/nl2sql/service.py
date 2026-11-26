"""问数服务层：只管编排与计时，护栏与 LLM 细节都在各自的模块里。"""

from __future__ import annotations

import time

from app.nl2sql import generator
from app.nl2sql.schemas import AskRead, ExampleRead

# 示例问题不是装饰：它们各自指向一张不同的表（ADS 现成指标 / DWS 自定义维度 /
# DWD 行级明细），用户点一遍就能建立"什么时候该下探到明细"的直觉。
EXAMPLES: tuple[ExampleRead, ...] = (
    ExampleRead(
        question="近 14 天每天的成交额、消耗和 ROAS 走势",
        table="ads_ops_daily",
        note="最简单的问法，走现成指标表",
    ),
    ExampleRead(
        question="按 7 天成交额排前 5 的店铺，各自的 ROAS 和转化率",
        table="ads_store_kpi",
        note="现成店铺 KPI，直接排序",
    ),
    ExampleRead(
        question="哪些商品既是低毛利又有库存风险？",
        table="ads_product_health",
        note="两个信号交叉，考的是同时用两个列做过滤",
    ),
    ExampleRead(
        question="近 7 天每个平台的点击率和转化率，按点击率降序",
        table="dws_store_daily",
        note="要按平台聚合，得下探到 DWS 自己算比率",
    ),
    ExampleRead(
        question="Agent 任务失败最多的那天是几号，失败率多少",
        table="dws_agent_daily",
        note="先聚合再相除，避免 AVG(比率) 的坑",
    ),
    ExampleRead(
        question="素材评分最高的 10 个商品，分别属于哪个创意方案类型",
        table="dwd_asset_fact",
        note="要看行级明细，落到 DWD",
    ),
)


def examples() -> list[ExampleRead]:
    return list(EXAMPLES)


def ask(workspace_id: int | None, question: str) -> AskRead:
    started = time.perf_counter()
    answer = generator.answer(workspace_id, question)
    return AskRead(
        question=question.strip(),
        sql=answer.sql,
        explanation=answer.explanation,
        assumptions=answer.assumptions,
        confidence=answer.confidence,
        columns=answer.result["columns"],
        rows=answer.result["rows"],
        row_count=answer.result["row_count"],
        truncated=answer.result["truncated"],
        max_rows=answer.result["max_rows"],
        attempts=answer.attempts,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
