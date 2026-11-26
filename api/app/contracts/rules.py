"""风险规则：只做能拿出数字或结构证据的判断。

这个文件的边界很清楚——**凡是需要"读懂"的判断都不在这里**，留给语义复核（ADR 0015）。
规则只回答"账期几天""有没有验收条款""违约金几个点"这类可以摊开核对的问题。

每条规则输出都必须带 `quote`：触发它的那一句原话。没有原句的命中一律不报——
审批人看不到出处，就只能选择相信或不信系统，两者都不是好结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.contracts.clauses import KINDS, Clause, title_of

RULE_VERSION = "1.0.0"

# 数字 + 量词的提取。中文数字在合同里很常见（"三十日"），不处理会漏一大半。
_NUM = r"(\d+|[一二三四五六七八九十百零〇]+)"
_DAYS = re.compile(_NUM + r"\s*(?:个?\s*)?(?:自然日|工作日|天|日)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")

# 账期超过这个天数就该谈。60 天是行业里"还行"的上限，90 天已经是拿供应商的钱做生意。
PAYMENT_DAYS_WARN = 60
PAYMENT_DAYS_BAD = 90

# 定金/首付超过这个比例，等于把资金压力全甩给乙方
DEPOSIT_PCT_WARN = 30.0

# 违约金超过合同金额这个比例，法院通常认为过分高于损失
PENALTY_PCT_WARN = 30.0

# 争议解决在境外：判决认可与执行的成本会高一个数量级
FOREIGN_FORUMS = ("香港", "新加坡", "伦敦", "纽约", "境外", "国外", "ICC", "CIETAC 香港", "HKIAC", "SIAC")

# 单方解除权的表述。只认"某方有权（单方）解除"这一种句式，不猜"可以解除"。
# 「也 / 均 / 同样」要放行，否则「甲方有权解除，乙方也有权解除」会被读成单方。
_UNILATERAL = re.compile(
    r"(甲|乙|采购|供应|买|卖)\s*方?\s*(?:也|均|同样|亦)?\s*(?:有权|可)\s*"
    r"(?:单方|单方面|随时)?\s*(?:解除|终止)"
)

# 自动续约。约定自动续约但没写提前通知期，等于把退出权交给沉默。
_AUTO_RENEW = re.compile(r"(自动续[约签期]|自动延[长期]|期满自动)")

_NOTICE = re.compile(_NUM + r"\s*(?:个?\s*)?(?:自然日|工作日|天|日)\s*(?:内|前)?\s*(?:书面)?\s*(?:通知|告知)")

_LIMITLESS = (
    "承担全部损失",
    "承担一切损失",
    "无限责任",
    "不设上限",
    "不以本合同金额为限",
    "超出合同金额",
    "全部赔偿责任",
)


@dataclass(frozen=True)
class Finding:
    rule_key: str
    severity: str
    title: str
    detail: str
    quote: str
    suggestion: str
    clause_ordinal: int | None
    clause_kind: str
    source: str = "rule"


def evaluate(clauses: list[Clause]) -> list[Finding]:
    """跑全部规则。顺序固定：报告每次生成的排列要一样，否则没法逐条对比两版。"""
    findings: list[Finding] = []
    for clause in clauses:
        findings.extend(_payment_days(clause))
        findings.extend(_deposit(clause))
        findings.extend(_penalty(clause))
        findings.extend(_unilateral(clause))
        findings.extend(_auto_renew(clause))
        findings.extend(_limitless(clause))
        findings.extend(_foreign_forum(clause))
    findings.extend(_missing(clauses))
    return findings


def _sentence(text: str, needle: str) -> str:
    """取 needle 所在的那一句。引整条条款太长，引几个字又看不出上下文。"""
    index = text.find(needle)
    if index < 0:
        return text.strip()[:160]
    start = max(text.rfind("。", 0, index), text.rfind("\n", 0, index), text.rfind("；", 0, index))
    start = 0 if start < 0 else start + 1
    end_candidates = [pos for pos in (text.find("。", index), text.find("\n", index)) if pos >= 0]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start : end + 1].strip()[:200]


def to_int(token: str) -> int | None:
    """把「30」「三十」「九十五」都变成整数。只处理 0-999，够用了。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)

    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if token == "十":
        return 10
    if "百" in token:
        head, _, tail = token.partition("百")
        hundreds = digits.get(head, 1) if head else 1
        rest = to_int(tail) if tail else 0
        return hundreds * 100 + (rest or 0)
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = digits.get(head, 1) if head else 1
        ones = digits.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(token) == 1:
        return digits.get(token)
    return None


def _percent_near(text: str, word: str, *, before: int = 60, after: int = 80) -> tuple[float, int] | None:
    """找 `word` 附近的百分比。

    窗口必须**双向**：合同里既写「定金 50%」也写「按合同金额的 40% 支付违约金」，
    只往后看会漏掉后一种——实测第一版就是这么漏的。
    """
    start = 0
    while True:
        index = text.find(word, start)
        if index < 0:
            return None
        window = text[max(0, index - before) : index + after]
        match = _PERCENT.search(window)
        if match:
            return float(match.group(1)), index
        start = index + 1


def _payment_days(clause: Clause) -> list[Finding]:
    if clause.kind != "payment":
        return []
    for match in _DAYS.finditer(clause.text):
        days = to_int(match.group(1))
        if days is None or days < PAYMENT_DAYS_WARN:
            continue
        # 「3 天内发货」也是"天"，但它不是账期：只有和付款/结算同句才算
        sentence = _sentence(clause.text, match.group(0))
        if not any(word in sentence for word in ("付款", "支付", "结算", "账期", "回款", "付清")):
            continue
        severity = "high" if days >= PAYMENT_DAYS_BAD else "medium"
        return [
            Finding(
                rule_key="payment_days_too_long",
                severity=severity,
                title=f"账期 {days} 天",
                detail=f"约定 {days} 天账期，超过 {PAYMENT_DAYS_WARN} 天的建议线。"
                f"账期越长，垫资压力和坏账敞口越大。",
                quote=sentence,
                suggestion=f"争取压到 {PAYMENT_DAYS_WARN} 天以内，或按节点分批结算（如 3:4:3）",
                clause_ordinal=clause.ordinal,
                clause_kind=clause.kind,
            )
        ]
    return []


def _deposit(clause: Clause) -> list[Finding]:
    if clause.kind != "payment":
        return []
    for word in ("定金", "首付", "预付款"):
        found = _percent_near(clause.text, word)
        if found is None:
            continue
        pct, _index = found
        if pct <= DEPOSIT_PCT_WARN:
            continue
        return [
            Finding(
                rule_key="deposit_too_high",
                severity="high" if pct >= 50 else "medium",
                title=f"{word} {pct:g}%",
                detail=f"{word}比例 {pct:g}%，超过 {DEPOSIT_PCT_WARN:g}% 的建议线。"
                "预付比例越高，交付前的资金风险越集中在一方。",
                quote=_sentence(clause.text, word),
                suggestion=f"压到 {DEPOSIT_PCT_WARN:g}% 以内，或改为按交付节点分期",
                clause_ordinal=clause.ordinal,
                clause_kind=clause.kind,
            )
        ]
    return []


def _penalty(clause: Clause) -> list[Finding]:
    if clause.kind != "breach":
        return []
    found = _percent_near(clause.text, "违约金")
    if found is None:
        return []
    pct, _index = found
    if pct <= PENALTY_PCT_WARN:
        return []
    return [
        Finding(
            rule_key="penalty_too_high",
            severity="high" if pct >= 50 else "medium",
            title=f"违约金 {pct:g}%",
            detail=f"违约金比例 {pct:g}%，超过合同金额的 {PENALTY_PCT_WARN:g}%。"
            "司法实践通常认为过分高于实际损失的违约金可被调整。",
            quote=_sentence(clause.text, "违约金"),
            suggestion=f"降到 {PENALTY_PCT_WARN:g}% 以内，或改为按实际损失赔偿并封顶",
            clause_ordinal=clause.ordinal,
            clause_kind=clause.kind,
        )
    ]


def _unilateral(clause: Clause) -> list[Finding]:
    subjects = {match.group(1) for match in _UNILATERAL.finditer(clause.text)}
    if len(subjects) >= 2:
        # 双方都有解除权，是对方也接受的对称条款，不算单方
        return []
    if not subjects:
        return []
    only = next(iter(subjects))
    other = "乙" if only == "甲" else ("甲" if only == "乙" else "对方")
    return [
        Finding(
            rule_key="unilateral_termination",
            severity="high",
            title="单方解除权",
            detail=f"只有「{only}方」可以解除/终止，另一方没有对等权利。"
            "这类条款在争议时会让被动一方失去退出手段。",
            quote=_sentence(clause.text, f"{only}方"),
            suggestion=f"改成双方对等：{other}方在同等条件下也应有权解除，或约定解除的补偿标准",
            clause_ordinal=clause.ordinal,
            clause_kind=clause.kind,
        )
    ]


def _auto_renew(clause: Clause) -> list[Finding]:
    match = _AUTO_RENEW.search(clause.text)
    if not match:
        return []
    if _NOTICE.search(clause.text):
        return []
    return [
        Finding(
            rule_key="auto_renewal_without_notice",
            severity="medium",
            title="自动续约且无通知期",
            detail="约定了自动续约，但没写提前多少天书面通知不续。"
            "没有通知期，等于把「要不要继续」的决定权交给日历。",
            quote=_sentence(clause.text, match.group(0)),
            suggestion="补一句「任一方可在期满前 30 日书面通知不再续约」",
            clause_ordinal=clause.ordinal,
            clause_kind=clause.kind,
        )
    ]


def _limitless(clause: Clause) -> list[Finding]:
    hit = next((word for word in _LIMITLESS if word in clause.text), None)
    if hit is None:
        return []
    return [
        Finding(
            rule_key="unlimited_liability",
            severity="high",
            title="赔偿责任不设上限",
            detail="约定了超出合同金额或不设上限的赔偿责任。"
            "合同金额是收益，赔偿不封顶意味着付出可能远超收益。",
            quote=_sentence(clause.text, hit),
            suggestion="加一句「除故意或重大过失外，赔偿责任以合同总金额为限」",
            clause_ordinal=clause.ordinal,
            clause_kind=clause.kind,
        )
    ]


def _foreign_forum(clause: Clause) -> list[Finding]:
    if clause.kind != "dispute":
        return []
    hit = next((word for word in FOREIGN_FORUMS if word in clause.text), None)
    if hit is None:
        return []
    return [
        Finding(
            rule_key="foreign_jurisdiction",
            severity="high",
            title=f"争议解决地：{hit}",
            detail=f"约定在「{hit}」解决争议。跨境仲裁与判决执行的周期和成本通常高一个数量级。",
            quote=_sentence(clause.text, hit),
            suggestion="改为境内仲裁机构（如上海国际经济贸易仲裁委员会）或被告所在地法院",
            clause_ordinal=clause.ordinal,
            clause_kind=clause.kind,
        )
    ]


def _missing(clauses: list[Clause]) -> list[Finding]:
    """缺条款也是风险，而且是最容易被忽略的一类——没写的东西不会报错。

    判"有没有"用的是**全文关键词**，不是条款分类。分类是按权重取最大的那个，
    一条"交付与验收"的条款会被归成 delivery——用分类判"缺验收"就会误报。
    """
    kinds = {clause.kind for clause in clauses}
    all_text = "\n".join(clause.text for clause in clauses)

    def present(kind: str) -> bool:
        if kind in kinds:
            return True
        return any(keyword in all_text for keyword in KINDS[kind][1])

    findings: list[Finding] = []

    if not present("dispute"):
        findings.append(
            Finding(
                rule_key="missing_dispute_clause",
                severity="medium",
                title="缺少争议解决条款",
                detail="全文没有仲裁/诉讼/管辖约定。真出事时，管辖地默认按法定规则定，通常不是你想要的那一个。",
                quote="（全文未出现：仲裁 / 诉讼 / 管辖 / 法院 / 适用法律）",
                suggestion="补一条：争议提交 XX 仲裁委员会仲裁，或由被告所在地有管辖权的法院管辖",
                clause_ordinal=None,
                clause_kind="dispute",
            )
        )
    if present("delivery") and not present("acceptance"):
        findings.append(
            Finding(
                rule_key="missing_acceptance_clause",
                severity="medium",
                title="有交付但没验收标准",
                detail="约定了交付，但没有验收方式与标准。验收标准缺失时，「是否合格」由付款方单方解释。",
                quote="（有交付条款，全文未出现：验收 / 质检 / 检验 / 合格标准）",
                suggestion="补一条验收条款：验收期限、抽样比例、判定标准，以及超期未提出异议视为验收合格",
                clause_ordinal=None,
                clause_kind="acceptance",
            )
        )
    if not present("breach"):
        findings.append(
            Finding(
                rule_key="missing_breach_clause",
                severity="medium",
                title="缺少违约责任条款",
                detail="全文没有违约责任约定。违约没有成本约定时，守约方只能举证实际损失。",
                quote="（全文未出现：违约 / 违约金 / 赔偿 / 罚则）",
                suggestion="补一条：逾期交付与逾期付款各自的违约金计算方式与上限",
                clause_ordinal=None,
                clause_kind="breach",
            )
        )
    return findings


def kind_title(kind: str) -> str:
    return title_of(kind)
