"""Prompt 组装。

这里只做字符串拼接，不放业务判断——判断在 generator.py。把 prompt 单独拆出来的
理由很实际：这是唯一一处"改一个词就可能让 SQL 变危险"的地方，值得能被单独读、
单独改、单独对照测试。
"""

from __future__ import annotations

_SYSTEM_RULES = """你是电商数据中台的问数助手。用户用中文提问，你把它翻译成一条 DuckDB SQL。

硬性规则（违反一次就会被拒绝，不要试探）：
1. 只输出**一条** `SELECT` 或 `WITH ... SELECT`，不写分号，不写注释，不做任何写操作。
2. 只能查下面目录里出现过的表；表名必须带 ods_ / dwd_ / dws_ / ads_ 前缀。
3. 列名只能用目录里出现的列名，不要臆造。**没有对应字段时，不要拿名字相近的列顶替**
   （典型错误：把 success_rate 直接别名为"失败率"）。能由现有字段推出来的就推出来
   （失败率 = `1 - success_rate`），推不出来的在 assumptions 里说明缺字段。
4. 能直接用 ADS 的现成指标就不要自己重算聚合；只有需要自定义维度时才下探到 DWS/DWD。
5. 计算比率一律"先聚合再相除"：`SUM(分子) / NULLIF(SUM(分母), 0)`，不要 `AVG(比率)`。
6. 涉及"近 N 天"时，窗口右端用仓内最新业务日期，不要用 `CURRENT_DATE`：
   `WHERE date > (SELECT max(date) FROM 表) - INTERVAL 'N days'`。
7. 需要可读排序时加 `ORDER BY`；聚合结果加 `LIMIT`（默认不超过 200 行）。
8. 结果列名用中文别名（`AS "成交额"`），方便非技术同事直接看。

只输出一个 json 对象，不要 markdown 代码块，不要前后缀说明：
{
  "sql": "SELECT ...",
  "explanation": "一句话说明这条 SQL 怎么算的（简体中文，不超过 60 字）",
  "assumptions": ["口径不确定时写下你的假设", "..."],
  "confidence": 0.0 到 1.0 之间的数字
}"""

_FEW_SHOT = """示例 1
问题：近 14 天每天的成交额和 ROAS 走势
{"sql": "SELECT date AS 日期, gmv AS 成交额, roas AS ROAS FROM ads_ops_daily WHERE date > (SELECT coalesce(max(date), DATE '1970-01-01') FROM ads_ops_daily) - INTERVAL '14 days' ORDER BY date", "explanation": "直接取 ADS 日指标表，窗口右端跟随仓内最新日期", "assumptions": [], "confidence": 0.9}

示例 2
问题：7 天成交额前 5 的店铺，各自的投放效率
{"sql": "SELECT store_name AS 店铺, platform AS 平台, gmv_7d AS 成交额, spend_7d AS 消耗, roas_7d AS ROAS FROM ads_store_kpi ORDER BY gmv_7d DESC LIMIT 5", "explanation": "ads_store_kpi 已是店铺 7 天 KPI，直接排序取前 5", "assumptions": ["按成交额降序理解为投放效率的观察顺序"], "confidence": 0.85}"""


def build_system_prompt(catalog: str, metric_hints: str) -> str:
    return "\n\n".join(
        [
            _SYSTEM_RULES,
            "## 可用表结构\n" + catalog,
            "## 口径\n" + metric_hints,
            "## 输出示例\n" + _FEW_SHOT,
        ]
    )


def build_messages(question: str, *, catalog: str, metric_hints: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt(catalog, metric_hints)},
        {"role": "user", "content": f"问题：{question}"},
    ]


def append_repair(
    messages: list[dict[str, str]],
    *,
    rejected_sql: str,
    reason: str,
) -> list[dict[str, str]]:
    """把上一轮的失败原因回灌给模型，要求它只改这一处。

    只回灌一句原因而不是整段护栏实现：模型需要知道边界在哪，不需要知道边界怎么写的
    ——把规则细节喂回去，等于给绕过规则提供素材。
    """
    return [
        *messages,
        {"role": "assistant", "content": f'{{"sql": {_json_quote(rejected_sql)}}}'},
        {
            "role": "user",
            "content": (
                f"这条 SQL 没能通过校验，原因：{reason}\n"
                "请只修正这个问题后重新给出完整 json（仍然只要一条 SELECT/WITH，"
                "仍然只用目录里的表和列）。"
            ),
        },
    ]


def _json_quote(value: str) -> str:
    """把 SQL 塞进 JSON 片段里做回灌；只处理引号和换行，够用且不需要引入 json。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'
