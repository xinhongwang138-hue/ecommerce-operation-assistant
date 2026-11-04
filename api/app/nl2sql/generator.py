"""生成 → 校验 → 执行 → 失败重来。

重试只有一轮（`MAX_ATTEMPTS = 2`），这不是省钱，是止损：如果第一次的失败原因是
"模型不认识你的表"，第二次通常还是不认识；如果原因是"写错了列名"，第二次基本就
对了。再往后属于系统性问题，该改的是 prompt 或目录，不是继续重试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import duckdb
from pydantic import BaseModel, Field, ValidationError

from app.dataplatform import service as platform_service
from app.dataplatform import warehouse
from app.nl2sql import catalog, prompt
from app.nl2sql.errors import NL2SQLRejected, WarehouseNotReady
from app.services.llm import create_json_chat_completion

MAX_ATTEMPTS = 2
MAX_QUESTION_CHARS = 400

# 模型爱把 SQL 包在 ```sql 里。这不是错误，只是包装，剥掉比报错便宜。
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


class SqlPlan(BaseModel):
    """模型必须交回的结构。字段全给默认值，只把 sql 设为必填。"""

    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.5


@dataclass
class Answer:
    sql: str
    explanation: str
    assumptions: list[str]
    confidence: float
    attempts: int
    result: dict[str, Any]


def answer(workspace_id: int | None, question: str) -> Answer:
    question = question.strip()
    if not question:
        raise NL2SQLRejected("问题不能为空", attempts=[])
    if len(question) > MAX_QUESTION_CHARS:
        raise NL2SQLRejected(f"问题过长，请控制在 {MAX_QUESTION_CHARS} 字以内", attempts=[])
    if not platform_service.is_ready(workspace_id):
        raise WarehouseNotReady("数据仓尚未构建")

    catalog_text = catalog.render_catalog(workspace_id)
    if not catalog_text:
        # 仓里有 meta_runs 但没有表结构，只可能是上一次跑批在事务里崩了。
        raise WarehouseNotReady("数据仓里没有可用表，请重新跑一次批")

    messages = prompt.build_messages(
        question,
        catalog=catalog_text,
        metric_hints=catalog.render_metric_hints(),
    )

    rejections: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        parsed, _raw = create_json_chat_completion(messages, max_tokens=900, temperature=0.1)
        try:
            plan = SqlPlan.model_validate(parsed)
        except ValidationError:
            rejections.append("模型没有返回可用的 sql 字段")
            messages = prompt.append_repair(
                messages, rejected_sql="", reason="返回的 json 里 sql 字段缺失或不是字符串"
            )
            continue

        sql = normalize_sql(plan.sql)
        if not sql:
            rejections.append("模型返回了空 SQL（可能这个问题不是取数问题）")
            messages = prompt.append_repair(
                messages, rejected_sql="", reason="sql 为空；如果问题与数据无关，请给出最接近的可查方案"
            )
            continue

        # 护栏先跑一遍：能把"越权/多语句"和"单纯写错列名"区分开，
        # 回给模型的修复提示才不会一律是"查询失败"。
        try:
            warehouse.guard_select(sql)
        except warehouse.QueryRejected as exc:
            rejections.append(f"护栏拒绝：{exc}")
            messages = prompt.append_repair(messages, rejected_sql=sql, reason=str(exc))
            continue

        try:
            result = warehouse.execute_guarded(workspace_id, sql)
        except duckdb.Error as exc:
            rejections.append(f"执行报错：{_short(exc)}")
            messages = prompt.append_repair(
                messages, rejected_sql=sql, reason=f"执行报错：{_short(exc)}"
            )
            continue

        return Answer(
            sql=sql,
            explanation=plan.explanation.strip(),
            assumptions=[item.strip() for item in plan.assumptions if item.strip()],
            confidence=max(0.0, min(1.0, float(plan.confidence))),
            attempts=attempt,
            result=result,
        )

    raise NL2SQLRejected(
        "两次都没有生成可执行的 SQL。可以换一种问法，或把问题拆小一点。",
        attempts=rejections,
    )


def normalize_sql(sql: str) -> str:
    """去掉代码围栏与结尾分号。只做这些：任何"顺手改 SQL"的尝试都在帮倒忙。"""
    cleaned = _FENCE.sub("", sql or "").strip()
    return cleaned.rstrip(";").rstrip()


def _short(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:300]
