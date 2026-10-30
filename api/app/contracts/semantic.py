"""语义复核：第二阶段，只找规则抓不到的那类风险。

规则能数清"账期 90 天"，数不清这些：

- 验收标准写着「以甲方确认为准」——没有数字，但付款方拿到了单方决定权；
- 义务表述「及时交付合格产品」——每个词都需要再解释一遍，等于没有约定；
- 付款与交付的耦合方式让一方先履约到底，另一方随时可以退。

这三类都需要读懂句子，所以交给模型。但模型有一个硬毛病：**它会编原文**。
所以本文件的核心不是 prompt，是那道 `quote` 校验（见 ADR 0015）——
模型给出的每一句引用都必须在合同正文里逐字出现，否则整条丢掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from app.contracts.clauses import Clause
from app.contracts.rules import Finding
from app.services.llm import create_json_chat_completion

# 一次最多接受几条。给模型一个上限，比事后截断更有效——不设限它会凑数。
MAX_SEMANTIC_RISKS = 5

# 送进模型的正文上限。超长合同只送前 N 字，并在 prompt 里说明被截断了，
# 不能悄悄截——那会让模型对没看到的部分下结论。
MAX_PROMPT_CHARS = 12000

_SEVERITIES = ("high", "medium", "low")


class SemanticRisk(BaseModel):
    title: str = ""
    detail: str = ""
    quote: str = ""
    severity: str = "medium"
    suggestion: str = ""


class SemanticReview(BaseModel):
    summary: str = ""
    risks: list[SemanticRisk] = Field(default_factory=list)


@dataclass(frozen=True)
class SemanticResult:
    summary: str
    findings: list[Finding]
    # 被丢掉的条数与原因：这一步必须可解释，否则"模型没看出问题"和"模型瞎说被过滤了"
    # 在报告里长得一样。
    dropped: int
    dropped_reason: str


def review(text: str, clauses: list[Clause], rule_findings: list[Finding]) -> SemanticResult:
    prompt_text = text[:MAX_PROMPT_CHARS]
    truncated = len(text) > MAX_PROMPT_CHARS

    messages = build_messages(prompt_text, rule_findings, truncated=truncated)
    parsed, _raw = create_json_chat_completion(messages, max_tokens=1600, temperature=0.2)
    try:
        review_obj = SemanticReview.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"模型返回的结构不符合约定：{exc}") from exc

    haystack = _normalize_for_match(text)
    findings: list[Finding] = []
    dropped = 0
    reasons: list[str] = []

    for item in review_obj.risks:
        if len(findings) >= MAX_SEMANTIC_RISKS:
            dropped += 1
            reasons.append("超出条数上限")
            continue
        quote = item.quote.strip()
        title = item.title.strip()
        if not title or not quote:
            dropped += 1
            reasons.append("缺标题或缺引用")
            continue
        if _normalize_for_match(quote) not in haystack:
            # 关键的一道闸：引用不是原文，说明这条是编的，整条丢掉
            dropped += 1
            reasons.append("引用不在原文中")
            continue
        if any(finding.quote and finding.quote in quote for finding in findings):
            continue
        ordinal, kind = _locate(clauses, quote)
        findings.append(
            Finding(
                rule_key="semantic_risk",
                severity=item.severity if item.severity in _SEVERITIES else "medium",
                title=title[:120],
                detail=item.detail.strip()[:600] or title[:120],
                quote=quote[:600],
                suggestion=item.suggestion.strip()[:400] or "与对方协商修改该条表述",
                clause_ordinal=ordinal,
                clause_kind=kind,
                source="llm",
            )
        )

    return SemanticResult(
        summary=review_obj.summary.strip(),
        findings=findings,
        dropped=dropped,
        dropped_reason="；".join(sorted(set(reasons))),
    )


def build_messages(text: str, rule_findings: list[Finding], *, truncated: bool) -> list[dict[str, str]]:
    already = (
        "\n".join(f"- [{item.severity}] {item.title}：{item.quote}" for item in rule_findings)
        or "（无）"
    )
    truncation_note = (
        "\n注意：合同较长，下面只是前 12000 字，末尾部分你没有看到，不要对其下结论。"
        if truncated
        else ""
    )
    system = (
        "你是采购方的合同审阅人。规则引擎已经查过可量化的条款，你的任务只有一件事："
        "找出规则抓不到的语义风险。\n\n"
        "只关注这四类：\n"
        "1. 把判断权交给一方（如「以甲方确认为准」「甲方有权认定」）；\n"
        "2. 义务表述含糊到无法执行（如「及时」「适当」「合格产品」而无标准）；\n"
        "3. 权利义务明显不对等（一方可随时变更，另一方只能接受）；\n"
        "4. 付款与交付的耦合让一方先履约到底。\n\n"
        "硬性要求：\n"
        "- 每条必须给 quote，且 quote 必须是合同原文里**逐字出现**的片段（10-60 字）。"
        "引用不到原文的条目会被程序直接丢弃，等于没写。\n"
        "- 不要重复规则已经报过的内容。\n"
        f"- 最多 {MAX_SEMANTIC_RISKS} 条。宁少勿凑。\n"
        "- 输出 JSON：{\"summary\": \"一句话总评\", \"risks\": "
        "[{\"title\", \"detail\", \"quote\", \"severity\": \"high|medium|low\", \"suggestion\"}]}"
    )
    user = (
        f"规则已命中的风险：\n{already}\n\n"
        f"合同正文：\n{text}{truncation_note}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _normalize_for_match(text: str) -> str:
    """比对前把空白差异抹平。

    模型经常把原文里的换行变成空格，或者把全角引号改成半角。这类差异不该算"编造"，
    但除此之外一个字都不放过。
    """
    return re.sub(r"\s+", "", (text or "")).replace("“", '"').replace("”", '"')


def _locate(clauses: list[Clause], quote: str) -> tuple[int | None, str]:
    needle = _normalize_for_match(quote)
    for clause in clauses:
        if needle and needle in _normalize_for_match(clause.text):
            return clause.ordinal, clause.kind
    return None, ""
