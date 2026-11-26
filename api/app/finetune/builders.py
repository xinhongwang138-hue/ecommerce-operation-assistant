"""样本构建：把各模块的原始记录变成训练样本。

三个判断，每个都要能说清为什么：

1. **多轮会话整段进，不拆成单轮。** 客服的语气和上下文有关（"那这个呢"这种追问
   单拎出来没有意义）。拆单轮看起来样本数翻倍，实际是让模型学不到对话。
2. **质量分层，不混成一个池子。** 得 A 的会话是"这样答对"，被拒答/转人工的会话是
   "什么时候该承认不知道"，低分会话是"待修"，三类混在一起训，模型会学到平均语气。
3. **入库前去重 + 长度过滤。** 同一句模板话术在不同场次出现几十次是常态，
   直接进训练集就是对那句话过拟合。过短的（"在的"）和不带上下文的也不要。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

BUILDER_VERSION = "1.0.0"

# 对话本身的字符数上下限（不含系统提示）。
# 下限压到 12：客服的一问一答常常很短（"什么时候发货""明天发"），
# 按 20 字卡会把大量真实短对话挡在外面，剩下的全是长会话，分布就偏了。
MIN_CHARS = 12
MAX_CHARS = 4000

# 一条会话最多取多少轮。太长的会话对训练没有额外价值，只是把 token 烧掉。
MAX_TURNS = 12

# 质量分层。这三个词是给人和脚本看的，不是给模型看的。
QUALITY_POSITIVE = "positive"  # 这样答是对的
QUALITY_BOUNDARY = "boundary"  # 这样承认不知道是对的
QUALITY_NEEDS_FIX = "needs_fix"  # 按扣分项标出来的待修样本

QUALITY_TITLES = {
    QUALITY_POSITIVE: "正向样本",
    QUALITY_BOUNDARY: "边界样本",
    QUALITY_NEEDS_FIX: "待修样本",
}

# 训练时的系统提示。和线上客服的口径保持一致，否则微调出来的模型和线上不是一个人。
SYSTEM_PROMPT = (
    "你是电商店铺的客服。回答要准确、简短、可核实；"
    "没有依据时直接说查不到并给出转人工入口，不要猜。"
)


@dataclass
class Draft:
    source: str
    source_ref: str
    quality: str
    messages: list[dict]
    tags: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        payload = json.dumps(self.messages, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def chars(self) -> int:
        """只数对话本身，不算系统提示。

        系统提示是固定模板（约 50 字），把它算进来会让 MIN_CHARS 永远命中不了——
        "在吗/在"这种两个字的会话也能通过长度过滤。实测踩过这个坑。
        """
        return sum(
            len(item.get("content") or "")
            for item in self.messages
            if item.get("role") != "system"
        )


def keep(draft: Draft | None) -> Draft | None:
    """长度过滤。返回 None 表示丢弃。"""
    if draft is None:
        return None
    if not draft.messages:
        return None
    if draft.chars() < MIN_CHARS or draft.chars() > MAX_CHARS:
        return None
    return draft


def from_qa_session(*, session_id: int, utterances: list[tuple[str, str]], grade: str, redline: bool) -> Draft | None:
    """从 P09 的质检会话构造样本。

    `utterances` 是 `[(role, content)]`，role 为 customer/agent。
    得 A/B 且非红线的进正向；C/D 或红线进待修——红线样本尤其重要，
    它们是"这样说是绝对不行的"的直接反例。
    """
    turns = [(role, text.strip()) for role, text in utterances if text and text.strip()][:MAX_TURNS]
    if not turns:
        return None

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, text in turns:
        messages.append({"role": "user" if role == "customer" else "assistant", "content": text})
    # SFT 样本最好以 assistant 结尾，否则模型学到"客服说完话客户又说一句"就断
    if messages[-1]["role"] != "assistant":
        return None

    tags = [f"grade:{grade}"]
    if redline:
        tags.append("redline")
        quality = QUALITY_NEEDS_FIX
    elif grade in ("A", "B"):
        quality = QUALITY_POSITIVE
    else:
        quality = QUALITY_NEEDS_FIX

    return keep(
        Draft(
            source="qa",
            source_ref=f"qa_session:{session_id}",
            quality=quality,
            messages=messages,
            tags=tags,
        )
    )


def from_cs_conversation(*, conversation_id: int, utterances: list[tuple[str, str]], track: str) -> Draft | None:
    """从 P01 的客服会话构造样本。

    `track` 决定质量分层：能直答/检索到的算正向，"拒答"和"转人工"算边界样本——
    它们教的是"什么时候该停"，这类样本在真实数据里最少，也最值钱。
    """
    turns = [(role, text.strip()) for role, text in utterances if text and text.strip()][:MAX_TURNS]
    if not turns:
        return None

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, text in turns:
        messages.append({"role": "user" if role == "customer" else "assistant", "content": text})
    if messages[-1]["role"] != "assistant":
        return None

    if track in ("refuse", "handoff"):
        quality = QUALITY_BOUNDARY
    elif track in ("faq", "retrieval"):
        quality = QUALITY_POSITIVE
    else:
        return None

    return keep(
        Draft(
            source="cs",
            source_ref=f"cs_conversation:{conversation_id}",
            quality=quality,
            messages=messages,
            tags=[f"track:{track}"],
        )
    )


def to_jsonl_line(messages: list[dict]) -> str:
    """导出行。用 OpenAI chat 格式——几乎所有训练框架都吃这个，
    自定义格式会让下游多写一层转换，那层转换就是错的地方。"""
    return json.dumps({"messages": messages}, ensure_ascii=False)
