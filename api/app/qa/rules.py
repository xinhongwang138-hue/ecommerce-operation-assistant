"""质检规则引擎：五条打分项 + 一条红线。

权重之和是 100，红线不参与加权——命中即总分归零。把红线做成"扣 30 分"是错的：
绝对化承诺（"百分百有效"）和私下交易（"加我微信转账"）不是"这轮做得不够好"，
而是不能发生，扣完还剩 70 分会让它挤进 B 级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

RULE_VERSION = "1.0.1"

# 等级线。60 分以下是 D：不是"差一点"，是这条会话需要重看一遍。
GRADE_THRESHOLDS = (("A", 85.0), ("B", 70.0), ("C", 60.0))

# 首响时效的两端：60 秒内满分，5 分钟外 0 分，中间线性。
FIRST_RESPONSE_GOOD_SECONDS = 60.0
FIRST_RESPONSE_BAD_SECONDS = 300.0

# 推进动作：客户问完，坐席有没有把话往下带。宁可名单短一点也不要含糊。
ACTION_PHRASES = (
    "下单", "链接", "优惠", "券", "活动", "包邮", "库存", "发货", "补货",
    "安排", "帮您", "给您", "建议您", "可以帮", "马上", "稍等", "跟进", "回访",
)

GREETING_PHRASES = ("您好", "你好", "在的", "欢迎", "很高兴")
CLOSING_PHRASES = ("感谢", "谢谢", "还有其他", "随时", "祝您", "再见")

# 红线：三类，任何一类命中即总分归零并单独标记。
REDLINE_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "absolute_promise": (
        "绝对化承诺",
        ("百分百", "100%", "保证", "绝对", "最便宜", "全网最低", "第一名", "永久", "无理由", "包治"),
    ),
    "private_deal": (
        "私下交易",
        ("加微信", "私聊", "私下", "转账", "支付宝", "扫码付", "红包"),
    ),
    "rudeness": (
        "失礼或推诿",
        ("滚开", "滚蛋", "滚出去", "滚远点", "闭嘴", "不管了", "爱买不买", "自己看"),
    ),
}

# 命中词被更长的正常词"吞掉"时不算踩线。
#
# 中文没有词边界，`phrase in text` 这种判断必然误伤。实测踩到的是：运营复盘
# 原文写着「第二版主图回滚」，规则把它判成坐席骂人「滚」，三条会话全部归零。
# 红线是一票否决，误判的代价不对称——一条正常回复被判红，整条会话作废，
# 而且报告里会赫然写着"失礼或推诿"，看的人第一反应是系统坏了。
#
# 处理方式不是删掉关键词（那样真骂人也不报了），而是**跳过被正常词覆盖的那次命中**。
REDLINE_BENIGN_COMPOUNDS: dict[str, tuple[str, ...]] = {
    "滚": ("回滚", "滚动", "滚轮", "翻滚", "滚存"),
    "保证": ("保证金", "保证书"),
    "无理由": ("天无理由",),  # 「7 天无理由退换」是消费者的法定权利，不是承诺
    "绝对": ("绝对值", "绝对路径"),
    "永久": ("永久链接", "永久保存"),
    "私下": ("私下沟通",),
}

# 问题类型：客户问的是哪一类事，以及回复里该出现什么才算答到了。
#
# 一开始用的是"客户原话的关键词有没有出现在回复里"，实测不可用：客户问
# 「这个低糖饼干还有货吗，多少钱」，坐席答「有的，29.9 元一盒，可以帮您留货」——
# 人看着完全答到了，但字面交集只有 0 个（回复没重复商品名），规则判 0 分。
# 换成"问题类型覆盖"后，判据变成"客户问价格，回复有没有报价"，既可解释又不脆。
INTENTS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "price": (
        "价格",
        ("多少钱", "价格", "贵", "便宜", "优惠", "折扣", "性价比", "包邮吗"),
        ("元", "价", "优惠", "折", "券", "活动", "便宜", "贵", "划算"),
    ),
    "stock": (
        "库存",
        ("有货", "库存", "还有吗", "没货", "补货", "缺货", "现货", "剩余", "断货"),
        ("有", "库存", "现货", "补货", "缺", "没", "留", "剩余", "到货"),
    ),
    "shipping": (
        "物流",
        ("发货", "快递", "几天到", "物流", "运费", "什么时候到", "多久能到"),
        ("发货", "快递", "物流", "运", "到", "天", "包邮", "发出"),
    ),
    "usage": (
        "用法",
        ("怎么用", "用法", "怎么吃", "用量", "适合", "能吃", "注意", "成分", "禁忌"),
        ("用", "吃", "量", "适合", "建议", "注意", "成分", "搭配", "次"),
    ),
    "aftersale": (
        "售后",
        ("退", "换", "退款", "坏", "破损", "售后", "保修", "投诉", "漏"),
        ("退", "换", "售后", "处理", "赔", "保修", "安排", "补发", "负责"),
    ),
}

EXCERPT_CHARS = 40

# 红线的改进建议。报告里排在所有建议之前——红线不是"下一轮优化项"，
# 是这一轮就要停下来的事。
REDLINE_ADVICE = {
    "absolute_promise": "绝对化承诺是红线：不要承诺效果（「百分百」「保证」「绝对」），改成描述可核实的事实",
    "private_deal": "私下交易是红线：任何引导站外转账、私聊、扫码付的表述都不能出现",
    "rudeness": "失礼或推诿是红线：这类回复按投诉处理，先解决态度再谈话术",
}


@dataclass(frozen=True)
class Utterance:
    """一句话。规则只看这三件事，所以不需要 ORM 实体。"""

    seq: int
    role: str  # customer / agent
    content: str
    sent_at: datetime | None = None


@dataclass(frozen=True)
class Evidence:
    seq: int
    role: str
    excerpt: str
    note: str


@dataclass
class RuleResult:
    key: str
    title: str
    dimension: str
    max_score: float
    score: float
    passed: bool
    detail: str
    advice: str
    evidence: list[Evidence] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "dimension": self.dimension,
            "max_score": round(self.max_score, 1),
            "score": round(self.score, 1),
            "passed": self.passed,
            "detail": self.detail,
            "advice": self.advice,
            "evidence": [
                {"seq": item.seq, "role": item.role, "excerpt": item.excerpt, "note": item.note}
                for item in self.evidence
            ],
        }


@dataclass
class ScoreCard:
    total: float
    grade: str
    breakdown: list[RuleResult]
    redline: bool
    redline_rules: list[str]
    rule_version: str = RULE_VERSION

    def as_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "grade": self.grade,
            "redline": self.redline,
            "redline_rules": self.redline_rules,
            "rule_version": self.rule_version,
            "breakdown": [item.as_dict() for item in self.breakdown],
        }


def score_session(messages: list[Utterance]) -> ScoreCard:
    """把一次会话打成一张计分卡。顺序固定，便于前后两次结果逐项对比。"""
    results = [
        _completeness(messages),
        _first_response(messages),
        _requirement_hit(messages),
        _next_action(messages),
        _courtesy(messages),
    ]
    raw_total = sum(item.score for item in results)

    hits = _redline_hits(messages)
    if hits:
        # 红线：保留逐项明细（报告要能说明"本来多少分"），但总分归零。
        return ScoreCard(
            total=0.0,
            grade="D",
            breakdown=results,
            redline=True,
            redline_rules=[key for key, _ in hits],
        )

    total = round(raw_total, 1)
    return ScoreCard(
        total=total,
        grade=_grade_of(total),
        breakdown=results,
        redline=False,
        redline_rules=[],
    )


def grade_of(total: float) -> str:
    return _grade_of(total)


def _grade_of(total: float) -> str:
    for grade, floor in GRADE_THRESHOLDS:
        if total >= floor:
            return grade
    return "D"


def _customers(messages: list[Utterance]) -> list[Utterance]:
    return [item for item in messages if item.role == "customer"]


def _agents(messages: list[Utterance]) -> list[Utterance]:
    return [item for item in messages if item.role == "agent"]


def _excerpt(text: str) -> str:
    clipped = " ".join(text.split())
    return clipped[:EXCERPT_CHARS] + ("…" if len(clipped) > EXCERPT_CHARS else "")


def _completeness(messages: list[Utterance]) -> RuleResult:
    customers = _customers(messages)
    agents = _agents(messages)
    if not customers:
        return RuleResult(
            key="completeness",
            title="响应完整性",
            dimension="服务",
            max_score=30.0,
            score=0.0,
            passed=False,
            detail="会话里没有客户发言，无法判断是否被回复",
            advice="确认导入时角色映射是否正确（客户应为 customer）",
        )

    unanswered: list[Utterance] = []
    for customer in customers:
        if not any(agent.seq > customer.seq for agent in agents):
            unanswered.append(customer)

    answered = len(customers) - len(unanswered)
    ratio = answered / len(customers)
    return RuleResult(
        key="completeness",
        title="响应完整性",
        dimension="服务",
        max_score=30.0,
        score=round(30.0 * ratio, 1),
        passed=not unanswered,
        detail=(
            f"{len(customers)} 条客户发言中 {len(unanswered)} 条没有得到回复"
            if unanswered
            else f"{len(customers)} 条客户发言全部得到回复"
        ),
        advice="" if not unanswered else "每条客户提问都要有回应；答不上来也要给一句明确的后续时间",
        evidence=[
            Evidence(seq=item.seq, role=item.role, excerpt=_excerpt(item.content), note="这条客户发言之后没有坐席回复")
            for item in unanswered[:2]
        ],
    )


def _first_response(messages: list[Utterance]) -> RuleResult:
    first_customer = next((item for item in messages if item.role == "customer"), None)
    if first_customer is None:
        return RuleResult(
            key="first_response",
            title="首响时效",
            dimension="效率",
            max_score=20.0,
            score=0.0,
            passed=False,
            detail="会话里没有客户发言，无法计算首响时效",
            advice="确认导入的角色映射与时间戳",
        )
    first_agent = next(
        (item for item in messages if item.role == "agent" and item.seq > first_customer.seq),
        None,
    )
    if first_agent is None:
        return RuleResult(
            key="first_response",
            title="首响时效",
            dimension="效率",
            max_score=20.0,
            score=0.0,
            passed=False,
            detail="客户发言后没有任何坐席回复",
            advice="先解决「不回复」的问题，再谈快慢",
            evidence=[
                Evidence(seq=first_customer.seq, role="customer", excerpt=_excerpt(first_customer.content), note="未得到回复")
            ],
        )
    if first_customer.sent_at is None or first_agent.sent_at is None:
        # 不猜：缺时间戳时给 0 并说明，而不是假设"大概挺快的"。
        return RuleResult(
            key="first_response",
            title="首响时效",
            dimension="效率",
            max_score=20.0,
            score=0.0,
            passed=False,
            detail="缺少消息时间戳，无法计算首响时效（不猜）",
            advice="导入时带上每条消息的时间，否则这一项永远算不出来",
            evidence=[
                Evidence(seq=first_customer.seq, role="customer", excerpt=_excerpt(first_customer.content), note="缺时间戳"),
                Evidence(seq=first_agent.seq, role="agent", excerpt=_excerpt(first_agent.content), note="缺时间戳"),
            ],
        )

    delay = (first_agent.sent_at - first_customer.sent_at).total_seconds()
    if delay <= FIRST_RESPONSE_GOOD_SECONDS:
        ratio = 1.0
    elif delay >= FIRST_RESPONSE_BAD_SECONDS:
        ratio = 0.0
    else:
        span = FIRST_RESPONSE_BAD_SECONDS - FIRST_RESPONSE_GOOD_SECONDS
        ratio = (FIRST_RESPONSE_BAD_SECONDS - delay) / span
    return RuleResult(
        key="first_response",
        title="首响时效",
        dimension="效率",
        max_score=20.0,
        score=round(20.0 * ratio, 1),
        passed=delay <= FIRST_RESPONSE_GOOD_SECONDS,
        detail=f"首次回复用时 {delay:.0f} 秒（{FIRST_RESPONSE_GOOD_SECONDS:.0f} 秒内满分）",
        advice="" if delay <= FIRST_RESPONSE_GOOD_SECONDS else "超过 1 分钟才回第一句，客户已经在别处下单了",
        evidence=[
            Evidence(seq=first_customer.seq, role="customer", excerpt=_excerpt(first_customer.content), note="客户提问"),
            Evidence(seq=first_agent.seq, role="agent", excerpt=_excerpt(first_agent.content), note=f"{delay:.0f} 秒后首次回复"),
        ],
    )


def _requirement_hit(messages: list[Utterance]) -> RuleResult:
    customers = _customers(messages)
    agents = _agents(messages)
    if not customers:
        return RuleResult(
            key="requirement_hit",
            title="需求命中",
            dimension="专业",
            max_score=25.0,
            score=0.0,
            passed=False,
            detail="会话里没有客户发言，无法判断需求是否被回应",
            advice="确认导入的角色映射",
        )
    if not agents:
        return RuleResult(
            key="requirement_hit",
            title="需求命中",
            dimension="专业",
            max_score=25.0,
            score=0.0,
            passed=False,
            detail="没有任何坐席回复",
            advice="先解决「不回复」的问题",
        )

    customer_text = " ".join(item.content for item in customers)
    agent_text = " ".join(item.content for item in agents)
    asked = [
        (key, title)
        for key, (title, triggers, _answers) in INTENTS.items()
        if any(trigger in customer_text for trigger in triggers)
    ]
    if not asked:
        return RuleResult(
            key="requirement_hit",
            title="需求覆盖",
            dimension="专业",
            max_score=25.0,
            score=0.0,
            passed=False,
            detail="没有识别出客户在问哪一类问题，无法判断是否答到（不猜）",
            advice="会话太短或只有寒暄，这一项没有参考价值",
        )

    missed = [
        (key, title)
        for key, title in asked
        if not any(hint in agent_text for hint in INTENTS[key][2])
    ]
    ratio = (len(asked) - len(missed)) / len(asked)
    return RuleResult(
        key="requirement_hit",
        title="需求覆盖",
        dimension="专业",
        max_score=25.0,
        score=round(25.0 * ratio, 1),
        passed=not missed,
        detail=(
            f"客户问了 {len(asked)} 类问题（{'、'.join(title for _key, title in asked)}），"
            f"回复覆盖了 {len(asked) - len(missed)} 类"
        ),
        advice="" if not missed else f"客户在问「{'、'.join(title for _key, title in missed)}」，回复里没有对应内容",
        evidence=(
            [
                Evidence(
                    seq=customers[0].seq,
                    role="customer",
                    excerpt=_excerpt(customers[0].content),
                    note="问题出处",
                ),
                Evidence(
                    seq=agents[0].seq,
                    role="agent",
                    excerpt=_excerpt(agents[0].content),
                    note=f"没有回应「{'、'.join(title for _key, title in missed)}」",
                ),
            ]
            if missed
            else []
        ),
    )


def _next_action(messages: list[Utterance]) -> RuleResult:
    agents = _agents(messages)
    if not agents:
        return RuleResult(
            key="next_action",
            title="推进动作",
            dimension="转化",
            max_score=15.0,
            score=0.0,
            passed=False,
            detail="没有任何坐席回复",
            advice="先解决「不回复」的问题",
        )
    for agent in agents:
        hit = next((phrase for phrase in ACTION_PHRASES if phrase in agent.content), None)
        if hit is not None:
            return RuleResult(
                key="next_action",
                title="推进动作",
                dimension="转化",
                max_score=15.0,
                score=15.0,
                passed=True,
                detail=f"回复中包含推进动作「{hit}」",
                advice="",
                evidence=[Evidence(seq=agent.seq, role="agent", excerpt=_excerpt(agent.content), note=f"命中「{hit}」")],
            )
    return RuleResult(
        key="next_action",
        title="推进动作",
        dimension="转化",
        max_score=15.0,
        score=0.0,
        passed=False,
        detail="整段会话没有任何推进动作（链接 / 优惠 / 下单 / 跟进时间）",
        advice="答完问题要带一句下一步，否则客户问完就走了",
        evidence=[
            Evidence(seq=agents[-1].seq, role="agent", excerpt=_excerpt(agents[-1].content), note="最后一句话也没有推进")
        ],
    )


def _courtesy(messages: list[Utterance]) -> RuleResult:
    agents = _agents(messages)
    if not agents:
        return RuleResult(
            key="courtesy",
            title="礼貌用语",
            dimension="服务",
            max_score=10.0,
            score=0.0,
            passed=False,
            detail="没有任何坐席回复",
            advice="先解决「不回复」的问题",
        )
    opening = agents[0]
    closing = agents[-1]
    has_open = any(phrase in opening.content for phrase in GREETING_PHRASES)
    has_close = any(phrase in closing.content for phrase in CLOSING_PHRASES)
    score = (5.0 if has_open else 0.0) + (5.0 if has_close else 0.0)
    missing = []
    if not has_open:
        missing.append("开场问候")
    if not has_close:
        missing.append("结束致谢")
    evidence = []
    for item, hit, note_when_miss in ((opening, has_open, "首句没有问候"), (closing, has_close, "末句没有收尾")):
        if not hit:
            evidence.append(
                Evidence(seq=item.seq, role="agent", excerpt=_excerpt(item.content), note=note_when_miss)
            )
    return RuleResult(
        key="courtesy",
        title="礼貌用语",
        dimension="服务",
        max_score=10.0,
        score=score,
        passed=not missing,
        detail="开场问候与结束致谢都齐全" if not missing else f"缺少{'、'.join(missing)}",
        advice="" if not missing else "开头一句问候、结尾一句「还有其他可以帮您吗」，成本很低",
        evidence=evidence,
    )


def _find_redline_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    """在坐席回复里找第一个真正踩线的词，跳过被正常词吞掉的命中。"""
    for phrase in phrases:
        if _has_real_hit(text, phrase):
            return phrase
    return None


def _has_real_hit(text: str, phrase: str) -> bool:
    benign = REDLINE_BENIGN_COMPOUNDS.get(phrase, ())
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return False
        if not _covered_by_benign(text, index, len(phrase), benign):
            return True
        start = index + 1


def _covered_by_benign(text: str, index: int, length: int, benign: tuple[str, ...]) -> bool:
    """这次命中是否整段落在某个正常词里面。"""
    for word in benign:
        cursor = text.find(word)
        while cursor >= 0:
            if cursor <= index and index + length <= cursor + len(word):
                return True
            cursor = text.find(word, cursor + 1)
    return False


def _redline_hits(messages: list[Utterance]) -> list[tuple[str, str]]:
    """红线只看坐席说的话。客户骂人不算坐席的错。"""
    hits: list[tuple[str, str]] = []
    for agent in _agents(messages):
        for key, (_title, phrases) in REDLINE_RULES.items():
            hit = _find_redline_phrase(agent.content, phrases)
            if hit is not None:
                hits.append((key, hit))
                break
    return hits


def redline_evidence(messages: list[Utterance]) -> list[dict]:
    """红线证据：报告里要能直接看到"哪句话踩了哪条线"。"""
    items: list[dict] = []
    for agent in _agents(messages):
        for key, (title, phrases) in REDLINE_RULES.items():
            hit = _find_redline_phrase(agent.content, phrases)
            if hit is not None:
                items.append(
                    {
                        "key": key,
                        "title": title,
                        "seq": agent.seq,
                        "excerpt": _excerpt(agent.content),
                        "phrase": hit,
                    }
                )
                break
    return items


def to_decimal(value: float) -> Decimal:
    """分数统一按 1 位小数落库，避免 62.00000000000001 这种尾巴进报表。"""
    return Decimal(str(round(value, 1)))
