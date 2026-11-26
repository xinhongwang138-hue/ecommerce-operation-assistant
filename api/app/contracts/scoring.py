"""风险分口径。**唯一真源**——报告里的分数、首页的分数、导出的分数都从这里出。

不按条数累加，按严重度加权：一份合同里 5 条"低"和 1 条"高"不是一回事，
按条数算会让前者看起来更危险。
"""

from __future__ import annotations

# 每条风险的权重。这三个数不是拍脑袋：high 单项就足以把一份合同推过
# "必须谈"的线，medium 要三条才够，low 基本只影响排序。
SEVERITY_WEIGHTS = {"high": 34, "medium": 12, "low": 4}

# 分数封顶 100。超过部分没有信息量，反而会让两份都很差的合同看起来一样。
MAX_SCORE = 100

# 等级线。high 只要一条：单项 34 分 + 一条 medium 就 46，已经该谈了。
LEVEL_THRESHOLDS = (("high", 50), ("medium", 20))


def score_of(severities: list[str]) -> int:
    total = sum(SEVERITY_WEIGHTS.get(item, 0) for item in severities)
    return min(MAX_SCORE, total)


def level_of(score: int) -> str:
    for level, floor in LEVEL_THRESHOLDS:
        if score >= floor:
            return level
    return "low"


def explain() -> list[dict]:
    """给前端"口径说明"用。分数是个数字，但审批人要能看到它怎么算出来的。

    `kind` 区分"权重"和"规则"：把封顶值和阈值混进权重列，表格就变成三个数
    加两个不相干的数，看的人会以为 50 也是一个权重。
    """
    return [
        {"kind": "weight", "severity": "high", "value": SEVERITY_WEIGHTS["high"], "note": "一条即可进入必须谈判"},
        {"kind": "weight", "severity": "medium", "value": SEVERITY_WEIGHTS["medium"], "note": "三条触及谈判线"},
        {"kind": "weight", "severity": "low", "value": SEVERITY_WEIGHTS["low"], "note": "只影响排序"},
        {"kind": "cap", "severity": "-", "value": MAX_SCORE, "note": "总分封顶 100"},
        {"kind": "threshold", "severity": "-", "value": 50, "note": "≥50 判 high、≥20 判 medium，其余 low"},
    ]
