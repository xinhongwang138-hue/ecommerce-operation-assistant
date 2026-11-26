"""依据判定与回复组装。

这个文件只回答两个问题：**这条检索结果够不够当作答案的依据**，以及**据此怎么写回复**。

阈值不是拍的，是实测出来的。用仓库里两篇运营资料，问四组相关问题与四组无关问题，
取 top1 的原始分数：

| 问题 | 词法分 | 向量相似度 |
|---|---|---|
| 素材哪一版更好 | 1.9199 | 0.3882 |
| 低糖饼干卖给谁 | 0.8979 | 0.7030 |
| 竞品价格怎么应对 | 2.1995 | 0.6044 |
| 缺货要不要紧 | — | 0.4338 |
| 我要退货怎么操作 | — | 0.3234 |
| 快递几天能到 | — | 0.3378 |
| 你是谁开发的 | — | 0.2590 |
| 今天天气怎么样 | — | 0.2152 |

相关最低 0.3882、无关最高 0.3378，所以拒答线取中间点 **0.36**。这个数字会随语料
变化，所以它和这张表放在一起：改阈值的人应该先重跑一遍这张表。

词法命中不设分数阈值，因为 BM25 的分数量纲随语料长度变，跨资料不可比；只要命中
了实词（`matched_terms` 非空）就算有字面依据。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.cs import faq, intents
from app.rag.schemas import HitRead

# 向量相似度下限。低于它视为"语义上也没沾边"。
VECTOR_FLOOR = 0.36

# 回复里最多带几条引用。带太多等于把检索结果倒给客户。
MAX_CITATIONS = 2


@dataclass(frozen=True)
class Evidence:
    """一条可被引用的依据。`kind` 决定回复怎么写，也决定前端怎么展示。"""

    kind: str  # faq / rag
    label: str
    snippet: str
    score: float | None = None


def pick_from_hits(hits: list[HitRead]) -> tuple[HitRead | None, Evidence | None, str]:
    """从检索结果里挑一条能用为依据的。

    返回 (命中的 hit, 依据, 拒答原因)。没有可用依据时 hit 为 None，原因要能读懂。
    """
    if not hits:
        return None, None, "知识库里没有可检索的内容"

    for hit in hits:
        # 词法命中：字面有实词重合，算有依据，不看分数
        if hit.matched_terms:
            return hit, _evidence_of(hit), ""
        # 纯向量命中：必须过相似度下限，否则就是"语义上也只是沾了个边"
        if hit.vector_score is not None and hit.vector_score >= VECTOR_FLOOR:
            return hit, _evidence_of(hit), ""

    best = hits[0]
    similarity = best.vector_score
    detail = f"（最接近的一条相似度 {similarity:.2f}，低于 {VECTOR_FLOOR:.2f}）" if similarity else ""
    return None, None, f"知识库里没有足够相关的内容{detail}"


def _evidence_of(hit: HitRead) -> Evidence:
    return Evidence(
        kind="rag",
        label=f"{hit.citation.document_title} 第 {hit.citation.ordinal + 1} 片",
        snippet=hit.snippet,
        score=hit.vector_score if hit.vector_score is not None else None,
    )


def compose(
    *,
    track: intents.Track,
    question: str,
    faq_item: faq.FaqItem | None = None,
    hit: HitRead | None = None,
    refuse_reason: str = "",
) -> tuple[str, list[Evidence]]:
    """按轨道写回复。模板很朴素——客服回复不需要文采，需要准确和信息完整。"""
    if track is intents.Track.FAQ and faq_item is not None:
        return (
            f"{faq_item.answer}\n\n如果还有其它想问的，直接说；需要人工我也可以帮您转。",
            [Evidence(kind="faq", label=f"FAQ：{faq_item.question}", snippet=faq_item.answer)],
        )

    if track is intents.Track.RETRIEVAL and hit is not None:
        evidence = _evidence_of(hit)
        citations = [evidence]
        extra = ""
        if hit.citation is not None and hit.content:
            body = hit.content.strip()
            extra = f"\n\n原文摘录：{body}"
        return (
            f"根据我这边查到的资料，可以这样理解：\n\n{hit.snippet}{extra}"
            f"\n\n（依据：{evidence.label}）如果和您的情况不完全一致，我可以帮您转人工。",
            citations[:MAX_CITATIONS],
        )

    if track is intents.Track.REFUSE:
        return (
            f"这个问题我查不到可靠的依据，先不猜答案。{refuse_reason}。\n\n"
            "您可以把具体情况说得再细一点，或者我直接帮您转人工客服。",
            [],
        )

    # 兜底分支：理论上走不到，走到了也要说清而不是硬编一句话
    return (
        "我这边这会儿没能确认答案，帮您转人工更稳妥。",
        [],
    )
