"""指标口径注册表。

口径只在这里定义一次，API、前端、问数（P03）、报告都从同一处取；`meta_metrics`
在每次跑批时落一份快照，于是"当时那个数是怎么算的"可回溯，不随代码改版消失。

每个指标都写明 `formula`，因为"ROAS 分母是消耗还是含税消耗"这类分歧，
靠字段名是看不出来的——只能靠一句话写死。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    unit: str
    direction: str
    layer: str
    source_table: str
    definition: str
    formula: str
    sql: str


def _daily(table: str, column: str) -> str:
    """日粒度序列：右端取仓内最新日期，避免离线补录被当成"最近无数据"。"""
    return f"""
        SELECT date AS period, {column} AS value
        FROM {table}
        WHERE date > (SELECT coalesce(max(date), DATE '1970-01-01') FROM {table})
                        - INTERVAL '{{days}} days'
        ORDER BY date
    """


METRICS: dict[str, Metric] = {
    metric.key: metric
    for metric in (
        Metric(
            key="gmv",
            name="成交额",
            unit="元",
            direction="up",
            layer="ADS",
            source_table="ads_ops_daily",
            definition="所选窗口内各店铺经营数据 revenue 之和，按自然日归集到 period_start。",
            formula="SUM(performance_records.revenue)",
            sql=_daily("ads_ops_daily", "gmv"),
        ),
        Metric(
            key="spend",
            name="投放消耗",
            unit="元",
            direction="down",
            layer="ADS",
            source_table="ads_ops_daily",
            definition="所选窗口内广告消耗之和；未填消耗的记录按 0 计入，不参与 ROAS 分母。",
            formula="SUM(performance_records.spend)",
            sql=_daily("ads_ops_daily", "spend"),
        ),
        Metric(
            key="roas",
            name="ROAS",
            unit="倍",
            direction="up",
            layer="ADS",
            source_table="ads_ops_daily",
            definition="先按日汇总成交额与消耗再相除；消耗为 0 时返回空值，不返回 0 或无穷大。",
            formula="SUM(revenue) / NULLIF(SUM(spend), 0)",
            sql=_daily("ads_ops_daily", "roas"),
        ),
        Metric(
            key="ctr",
            name="点击率",
            unit="%",
            direction="up",
            layer="ADS",
            source_table="ads_ops_daily",
            definition="点击总量 ÷ 曝光总量。先聚合再相除，避免小曝光记录把均值拉高。",
            formula="SUM(clicks) / NULLIF(SUM(impressions), 0)",
            sql=_daily("ads_ops_daily", "ctr"),
        ),
        Metric(
            key="cvr",
            name="转化率",
            unit="%",
            direction="up",
            layer="ADS",
            source_table="ads_ops_daily",
            definition="转化总量 ÷ 点击总量。口径与平台后台一致：分母是点击，不是曝光。",
            formula="SUM(conversions) / NULLIF(SUM(clicks), 0)",
            sql=_daily("ads_ops_daily", "cvr"),
        ),
        Metric(
            key="aov",
            name="客单价",
            unit="元",
            direction="up",
            layer="ADS",
            source_table="ads_ops_daily",
            definition="成交额 ÷ 转化数。转化数缺失时为 0，此时不产出客单价。",
            formula="SUM(revenue) / NULLIF(SUM(conversions), 0)",
            sql=_daily("ads_ops_daily", "aov"),
        ),
        Metric(
            key="low_stock_ratio",
            name="低库存 SKU 占比",
            unit="%",
            direction="down",
            layer="DWD",
            source_table="dwd_sku_dim",
            definition="可售量（库存−锁定）小于等于预警线的 SKU 占全部 SKU 的比例；快照指标，无日序列。",
            formula="COUNT(is_low_stock) / COUNT(sku_id)",
            sql="""
                SELECT (SELECT max(date) FROM ads_ops_daily) AS period,
                       CAST(sum(CASE WHEN is_low_stock THEN 1 ELSE 0 END) AS DOUBLE)
                           / NULLIF(count(*), 0) AS value
                FROM dwd_sku_dim
            """,
        ),
        Metric(
            key="agent_success_rate",
            name="Agent 任务成功率",
            unit="%",
            direction="up",
            layer="DWS",
            source_table="dws_agent_daily",
            definition="状态为 completed 的任务占当日全部任务的比例；canceled 计入分母。",
            formula="COUNT(status = 'completed') / COUNT(*)",
            sql=_daily("dws_agent_daily", "success_rate"),
        ),
    )
}

METRIC_KEYS: tuple[str, ...] = tuple(METRICS)


def get_metric(key: str) -> Metric | None:
    return METRICS.get(key)


def resolve_sql(metric: Metric, days: int) -> str:
    """days 夹在 1..90：太短看不出趋势，太长让后台点一次就把仓扫一遍。"""
    window = max(1, min(int(days), 90))
    return metric.sql.format(days=window)
