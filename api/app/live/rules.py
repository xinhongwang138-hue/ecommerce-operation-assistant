"""切片规则：找锚点 → 向两侧扩到合适时长 → 打分 → 去重。

时间轴一律用秒。转写里给的是 `{start, end, text}`，文本可能带标点也可能没有，
所以判断只看两件事：这段时间里说了什么，以及多长。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RULE_VERSION = "1.0.0"

# 短视频的黄金区间。短于 12 秒讲不完一个卖点，长于 60 秒完播率会掉。
MIN_SECONDS = 12
MAX_SECONDS = 60

# 从锚点向两侧扩展时，一次最多并进来几段。防止一句"好，那我们开始"把整场并成一片。
MAX_MERGE_SEGMENTS = 8

# 一次最多给多少个候选。给太多等于没挑——剪辑师不会看第 20 条。
MAX_CLIPS = 8

# 两个候选的重叠超过这个比例，后一个丢掉。
MAX_OVERLAP = 0.3

# 卖点词。分四组是因为它们的说服力不一样：价格锚点和限量比"好吃"硬得多。
# 权重之和就是 `_selling_points` 的上限。
SELLING_POINT_GROUPS: dict[str, tuple[int, tuple[str, ...]]] = {
    # 价格锚点：直接把数字摆出来
    "price": (12, ("只要", "到手价", "原价", "立减", "便宜", "划算", "元", "块钱", "折", "半价", "一元")),
    # 稀缺与紧迫：让人现在就下单
    "scarcity": (10, ("限量", "只剩", "最后", "抢完", "秒杀", "限时", "前多少", "手慢无", "库存")),
    # 权益与赠品
    "bonus": (8, ("赠", "送", "加赠", "包邮", "赠品", "买一送一", "第二件")),
    # 产品力：成分、口感、场景
    "product": (6, ("成分", "低糖", "无添加", "口感", "配料", "适合", "热量", "独立包装", "保质期")),
}

# 开场钩子：前 3 秒要能留住人。提问、数字、否定式都算。
_HOOK_PATTERNS = (
    re.compile(r"[？?]"),
    re.compile(r"^\s*(别|不要|千万别|是不是|有没有|为什么|怎么)"),
    re.compile(r"\d"),
)

# 行动号召：结尾得告诉人干什么
CTA_WORDS = ("下单", "点击", "购物车", "小黄车", "拍下", "去拍", "链接", "加购", "领券", "关注")

# 与切片无关的口播：寒暄、报时、催关注之外的闲聊。命中这些不直接扣分，
# 但会让"密度"分母变大——这正是我们想要的。
_FILLER = ("大家好", "欢迎", "稍等", "不好意思", "网络", "卡了", "听得到吗", "扣个 1")

# 打分权重，和上面四组一一对应。合计 100。
WEIGHTS = {"density": 40, "hook": 20, "cta": 20, "duration": 20}


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    transcript: str
    title: str
    hook: str
    score: int
    reasons: list[str]


def parse_transcript(raw: list) -> list[Segment]:
    """把外部传入的转写整成 `Segment` 列表，并按时间排序。

    容忍三种常见写法：`start/end`、`start_time/end_time`、以及只给 `start` 不给 `end`
    （此时按下一段的 start 收尾）。**不容忍缺 start**——没有时间码的句子对不上视频。
    """
    segments: list[Segment] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("start", item.get("start_time"))
        end = item.get("end", item.get("end_time"))
        try:
            start_int = int(float(start))
        except (TypeError, ValueError):
            continue
        try:
            end_int = int(float(end))
        except (TypeError, ValueError):
            end_int = start_int
        segments.append(Segment(start=start_int, end=max(end_int, start_int), text=text))

    segments.sort(key=lambda item: item.start)
    # 只给 start 的：用下一段的 start 补齐，最后一段按字数估 5 字/秒
    fixed: list[Segment] = []
    for index, item in enumerate(segments):
        if item.end <= item.start:
            if index + 1 < len(segments):
                end = segments[index + 1].start
            else:
                end = item.start + max(3, len(item.text) // 5)
            fixed.append(Segment(start=item.start, end=end, text=item.text))
        else:
            fixed.append(item)
    return fixed


def cut(segments: list[Segment], *, limit: int = MAX_CLIPS) -> list[Candidate]:
    """主流程。锚点取自"含卖点词"的段，向两侧扩到时长区间内，再按分排序去重。"""
    if not segments:
        return []

    anchors = [index for index, item in enumerate(segments) if _selling_hits(item.text)]
    if not anchors:
        # 没有卖点词就退一步：整场按固定窗口切，交给人工判断，总比返回空好
        anchors = list(range(0, len(segments), 3))

    candidates: list[Candidate] = []
    for index in anchors:
        window = _expand(segments, index)
        if window is None:
            continue
        candidate = _build(segments, window[0], window[1])
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-item.score, item.start))
    return _dedupe(candidates)[:limit]


def _expand(segments: list[Segment], anchor: int) -> tuple[int, int] | None:
    """从锚点向两侧扩，直到时长进入 [MIN, MAX]。

    先往右扩（把话说完整），右扩到上限再往左补（补上前因）。这个顺序是刻意的：
    切片的开头更该是"上一句的结尾"，而不是"下一句的开头"。
    """
    left = right = anchor
    for _ in range(MAX_MERGE_SEGMENTS):
        span = segments[right].end - segments[left].start
        if span >= MIN_SECONDS:
            break
        if right + 1 < len(segments) and (
            segments[right + 1].end - segments[left].start <= MAX_SECONDS
        ):
            right += 1
            continue
        if left - 1 >= 0 and (segments[right].end - segments[left - 1].start <= MAX_SECONDS):
            left -= 1
            continue
        break

    span = segments[right].end - segments[left].start
    if span > MAX_SECONDS:
        return None
    # 单段就超过上限的（有人一口气说了 90 秒），不硬切——切了会断句
    if span < MIN_SECONDS and right == left:
        return None
    return left, right


def _build(segments: list[Segment], left: int, right: int) -> Candidate | None:
    chunk = segments[left : right + 1]
    text = "".join(item.text for item in chunk).strip()
    if not text:
        return None
    start = chunk[0].start
    end = chunk[-1].end
    duration = max(1, end - start)

    hits = _selling_hits(text)
    density = _density_score(hits, duration)
    hook = _hook_score(text)
    cta = _cta_score(text)
    dur = _duration_score(duration)
    score = min(
        100,
        density * WEIGHTS["density"] // 100
        + hook * WEIGHTS["hook"] // 100
        + cta * WEIGHTS["cta"] // 100
        + dur * WEIGHTS["duration"] // 100,
    )

    reasons: list[str] = []
    if hits:
        reasons.append(f"卖点词 {len(hits)} 个：{'、'.join(hits[:6])}")
    if hook >= 80:
        reasons.append("开场 3 秒有钩子（提问/数字/否定式）")
    if cta >= 60:
        reasons.append("结尾有行动号召")
    if dur >= 80:
        reasons.append(f"时长 {duration} 秒，落在 {MIN_SECONDS}-{MAX_SECONDS} 秒区间")
    elif dur < 50:
        reasons.append(f"时长 {duration} 秒，偏短，完播容易但讲不清卖点")

    return Candidate(
        start=start,
        end=end,
        transcript=text,
        title=_title_of(text, hits),
        hook=_hook_line(chunk[0].text),
        score=score,
        reasons=reasons,
    )


def _selling_hits(text: str) -> list[str]:
    hits: list[str] = []
    for _group, (_weight, words) in SELLING_POINT_GROUPS.items():
        for word in words:
            if word in text and word not in hits:
                hits.append(word)
    return hits


def _density_score(hits: list[str], duration: int) -> int:
    """每 10 秒 2 个卖点词算满。低于 0.2 个/10 秒基本是闲聊。"""
    per_ten = len(hits) / max(1.0, duration / 10.0)
    return int(min(100, per_ten / 2.0 * 100))


def _hook_score(text: str) -> int:
    head = text[:40]
    hits = sum(1 for pattern in _HOOK_PATTERNS if pattern.search(head))
    return min(100, hits * 40)


def _cta_score(text: str) -> int:
    tail = text[-80:]
    hits = sum(1 for word in CTA_WORDS if word in tail)
    return min(100, hits * 40)


def _duration_score(duration: int) -> int:
    """落在区间中段（25-45 秒）满分，两端线性衰减。"""
    if 25 <= duration <= 45:
        return 100
    if duration < MIN_SECONDS or duration > MAX_SECONDS:
        return 20
    if duration < 25:
        return 60 + int((duration - MIN_SECONDS) / max(1, 25 - MIN_SECONDS) * 40)
    return 60 + int((MAX_SECONDS - duration) / max(1, MAX_SECONDS - 45) * 40)


def _title_of(text: str, hits: list[str]) -> str:
    """标题取第一句，超长截断。命中卖点时把词缀在后面，便于列表里扫读。"""
    first = re.split(r"[。！？!?]", text.strip(), maxsplit=1)[0].strip()
    title = first[:28] if first else text[:28]
    if hits:
        title = f"{title}（{'/'.join(hits[:3])}）"
    return title[:120]


def _hook_line(text: str) -> str:
    return " ".join(text.split())[:120]


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """重叠超过 30% 的只留分高的那个。先去重再截断，否则前 8 条可能全是同一句。"""
    kept: list[Candidate] = []
    for item in candidates:
        if any(_overlap(item, other) > MAX_OVERLAP for other in kept):
            continue
        kept.append(item)
    return kept


def _overlap(a: Candidate, b: Candidate) -> float:
    inner = min(a.end, b.end) - max(a.start, b.start)
    if inner <= 0:
        return 0.0
    shorter = min(a.end - a.start, b.end - b.start)
    return inner / max(1, shorter)
