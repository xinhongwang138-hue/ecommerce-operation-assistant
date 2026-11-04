"""意图三轨道：该转人工、能直答、要查资料。

判定顺序不是随意的：

1. **先判转人工**。客户说"我要投诉"的时候，先答一句"请问有什么可以帮您"是灾难。
   转人工信号优先级最高，代价只是偶尔多转一次。
2. **再看 FAQ**。确定性知识直接答，不经过检索排序。
3. **最后交给检索**。需要查资料（商品细节、活动规则、运营复盘）才走这条路。
4. **检索也不够就拒答**。这是 P01 与"什么都能聊两句"的机器人的分界线。

三条轨道之外还有一个结果：`refuse`。它不是轨道，是"没有依据"这个事实的名字。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Track(StrEnum):
    HANDOFF = "handoff"
    FAQ = "faq"
    RETRIEVAL = "retrieval"
    REFUSE = "refuse"


@dataclass(frozen=True)
class HandoffSignal:
    trigger: str
    title: str
    priority: str
    phrase: str
    reason: str


# 转人工触发词。分三类，优先级从高到低：明确要求 → 投诉/维权 → 情绪与僵局。
# 把"情绪"排在"要求"后面，是因为客户说"转人工，我要投诉"时，工单原因应该是投诉。
_HANDOFF_RULES: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    (
        "complaint",
        "投诉/维权",
        "high",
        ("投诉", "差评", "曝光", "举报", "12315", "退一赔三", "起诉", "骗子", "假货"),
        "客户表达投诉或维权意图，需人工介入处理",
    ),
    (
        "explicit_request",
        "要求人工",
        "high",
        ("转人工", "人工客服", "真人", "找人工", "客服电话", "叫你们主管", "找你们领导"),
        "客户明确要求人工服务",
    ),
    (
        "negative_emotion",
        "情绪负面",
        "normal",
        ("气死", "太差", "垃圾", "无语", "失望", "恶心", "什么破", "服了", "搞什么"),
        "客户情绪明显负面，先由人工安抚",
    ),
    (
        "escalation",
        "反复未解决",
        "normal",
        ("第二次", "第三次", "还没解决", "一直没", "问了半天", "又出问题"),
        "问题反复出现，自动回复已无法推进",
    ),
)


def detect_handoff(question: str) -> HandoffSignal | None:
    """按优先级匹配。命中即返回，不再往下看——转人工不需要"综合评分"。"""
    text = (question or "").strip()
    if not text:
        return None
    for trigger, title, priority, phrases, reason in _HANDOFF_RULES:
        hit = next((phrase for phrase in phrases if phrase in text), None)
        if hit is not None:
            return HandoffSignal(
                trigger=trigger,
                title=title,
                priority=priority,
                phrase=hit,
                reason=f"{reason}（命中「{hit}」）",
            )
    return None
