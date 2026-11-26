"""给模型看的仓结构目录。

不喂整份 DDL：建表语句里有 `CREATE OR REPLACE`、有完整 CASE 分支，token 花在
模型看不懂细节的地方，反而挤掉了示例问题。模型真正需要的是三件事：

1. 有哪些表、表在干什么（`meta_tables.description`，来自建表声明）；
2. 每张表有哪些列、什么类型；
3. 已经定义好的口径长什么样（`metrics.py`，别让模型自己发明 ROAS）。

`meta_*` 一律不进目录。那是跑批元数据（血缘、校验结果、口径快照），模型看到
`meta_quality` 就会去回答"数据质量怎么样"——而那个问题的正确入口是独立页面，
答案是 9 条校验，不是一段文字。
"""

from __future__ import annotations

import duckdb

from app.dataplatform import metrics as metric_registry
from app.dataplatform import warehouse

# 层序即优先级：先给 ADS，模型就更可能直接查现成指标而不是重写一遍聚合。
_LAYER_ORDER = {"ADS": 0, "DWS": 1, "DWD": 2, "ODS": 3}
_LAYER_NOTE = {
    "ADS": "现成指标，优先用",
    "DWS": "主题日汇总，做自定义维度时用",
    "DWD": "明细与维表，要行级细节时用",
    "ODS": "源表原样落地，尽量别直接用",
}
_EXCLUDED_PREFIX = "meta_"

# 字段名会说谎，类型不会解释。这里只登记**名字与含义不一致**的列，不是全量数据字典：
# 全部加注释等于没加注释，模型会把注意力平摊到 200 个列上。每条都必须能说清
# "只看列名会怎么错"——说不清的就不该进这张表。
_COLUMN_NOTES = {
    "dws_agent_daily.success_rate": "成功率（0-1），不是失败率；失败率 = 1 - success_rate",
    "dwd_task_fact.is_success": "任务成功为 true，失败为 false",
    "dwd_sku_dim.is_low_stock": "可用量 ≤ 预警阈值时为 true",
    "dwd_perf_fact.spend_missing": "该行没填消耗；spend=0 不代表真的没花钱",
    "dwd_perf_fact.roas": "行级 ROI 是 revenue/spend；多行汇总时不能 AVG 它",
    "ads_product_health.risk_label": "标签值（如 库存风险），不是布尔列",
    "ads_ops_summary.window_start": "窗口起点是 TIMESTAMP，终点是 DATE；跨类型比较要转换",
}


def _annotate(table: str, column: str) -> str:
    note = _COLUMN_NOTES.get(f"{table}.{column}")
    return f"{column}（{note}）" if note else column


def render_catalog(workspace_id: int | None) -> str:
    """把仓结构压成一段紧凑文本。仓为空时返回空串，由调用方决定怎么提示。"""
    rows = _columns(workspace_id)
    if not rows:
        return ""

    grouped: dict[str, dict[str, object]] = {}
    for table, column, data_type, layer, description, row_count in rows:
        entry = grouped.setdefault(
            table,
            {"layer": layer or "?", "description": description or "", "rows": row_count, "columns": []},
        )
        entry["columns"].append(f"{_annotate(table, column)} {data_type}")

    ordered = sorted(
        grouped.items(),
        key=lambda item: (_LAYER_ORDER.get(str(item[1]["layer"]), 9), item[0]),
    )

    blocks: list[str] = []
    for table, entry in ordered:
        layer = str(entry["layer"])
        header = f"### {table}｜{layer} {_LAYER_NOTE.get(layer, '')}｜{entry['rows']} 行"
        if entry["description"]:
            header += f"｜{entry['description']}"
        blocks.append(header)
        blocks.append("  " + ", ".join(entry["columns"]))  # type: ignore[arg-type]
    return "\n".join(blocks)


def render_metric_hints() -> str:
    """口径速查：让模型复用定义，而不是每次重新发明一个 ROAS。"""
    lines = ["### 已定义口径（能直接用就别自己算，口径不一致比算不出来更糟）"]
    for metric in metric_registry.METRICS.values():
        lines.append(
            f"- {metric.name}({metric.key})｜{metric.unit}｜取自 {metric.source_table}"
            f"｜口径：{metric.definition}｜公式：{metric.formula}"
        )
    return "\n".join(lines)


def table_names(workspace_id: int | None) -> list[str]:
    """目录里出现过的表名，供 prompt 里再强调一次"只能用这些"。"""
    return sorted({row[0] for row in _columns(workspace_id)})


def _columns(workspace_id: int | None) -> list[tuple[str, str, str, str | None, str | None, int | None]]:
    sql = """
        SELECT
            c.table_name,
            c.column_name,
            c.data_type,
            t.layer,
            t.description,
            t.row_count
        FROM information_schema.columns AS c
        LEFT JOIN meta_tables AS t ON t.table_name = c.table_name
        WHERE c.table_schema = 'main'
          AND c.table_name NOT LIKE ?
        ORDER BY c.table_name, c.ordinal_position
    """
    try:
        with warehouse.connect(workspace_id, read_only=True) as con:
            return [tuple(row) for row in con.execute(sql, [f"{_EXCLUDED_PREFIX}%"]).fetchall()]  # type: ignore[misc]
    except (duckdb.Error, FileNotFoundError):
        # 目录拿不到不是致命错误：宁可让模型在"没有表结构"的情况下被护栏挡住，
        # 也不要在这里把 500 抛给一个其实只是"还没跑批"的仓。
        return []
