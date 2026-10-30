"""把合同正文切成条款，并给每条条款归类。

切分的判据只有一条：**行首的编号**。中文合同的编号形式就那几种（第 X 条 / 一、/ 1.），
比"按段落切"稳得多——按空行切会把一条长条款拆成三段，风险评估就会把同一条风险报三遍。

`char_start` / `char_end` 指回原文，和 RAG 的切片同一套约定：报告里说"第 4 条"，
点开必须能落到那段字上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 条款编号的四种写法。顺序有讲究：先匹配"第X条"，否则"第一条"里的"一"会被当成"一、"。
_HEADING_PATTERNS = (
    re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*条[\s、.．:：]?"),
    re.compile(r"^\s*[（(]\s*[一二三四五六七八九十百零〇\d]+\s*[)）][\s、.．:：]?"),
    re.compile(r"^\s*[一二三四五六七八九十百零〇]+\s*[、.．][\s]?"),
    re.compile(r"^\s*\d+(?:\.\d+)*\s*[、.．)）][\s]?"),
    re.compile(r"^\s*#{1,4}\s+"),  # markdown 合同（从 .md 上传）
)

# 条款类型与它的识别词。和 P09 的 INTENTS 一样是手工短表：
# 宁可漏归到 other，也不要靠猜——归错类会让对应的规则查错地方。
KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "payment": ("付款", ("付款", "支付", "账期", "结算", "定金", "首付", "预付款", "价款", "发票", "汇款", "对公")),
    "delivery": ("交付", ("交付", "交货", "发货", "送货", "运输", "物流", "到货", "签收")),
    "acceptance": ("验收", ("验收", "质检", "检验", "抽检", "合格标准", "验收标准")),
    "breach": ("违约", ("违约", "赔偿", "罚则", "违约金", "滞纳金", "损失")),
    "termination": ("终止", ("解除", "终止", "退出", "解约", "到期", "不续")),
    "confidentiality": ("保密", ("保密", "商业秘密", "不得披露", "机密", "保密义务")),
    "ip": ("知识产权", ("知识产权", "著作权", "商标", "专利", "版权", "原创")),
    "dispute": ("争议解决", ("争议", "仲裁", "诉讼", "管辖", "法院", "适用法律")),
    "force_majeure": ("不可抗力", ("不可抗力", "自然灾害", "疫情", "政府行为")),
    "term": ("期限", ("有效期", "合同期限", "续约", "续签", "合作期限")),
}

OTHER = "other"
OTHER_TITLE = "其他"

# 太短的块不当条款（版权声明、页眉残留）。低于这个字数归到上一条。
MIN_CLAUSE_CHARS = 12


@dataclass(frozen=True)
class Clause:
    ordinal: int
    heading: str
    text: str
    kind: str
    char_start: int
    char_end: int


def split(text: str) -> list[Clause]:
    """按行首编号切条款。没有编号的合同整篇当成一条（也要能审）。"""
    body = text or ""
    if not body.strip():
        return []

    starts = _heading_offsets(body)
    if not starts:
        return [
            Clause(
                ordinal=1,
                heading="",
                text=body.strip(),
                kind=classify(body),
                char_start=0,
                char_end=len(body),
            )
        ]

    # 第一个编号之前的内容（标题、甲乙方）单独成一条"前言"，不丢
    blocks: list[tuple[int, int]] = []
    if starts[0][0] > 0 and body[: starts[0][0]].strip():
        blocks.append((0, starts[0][0]))
    for index, (offset, _line) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(body)
        blocks.append((offset, end))

    clauses: list[Clause] = []
    for start, end in blocks:
        chunk = body[start:end]
        if not chunk.strip():
            continue
        if len(chunk.strip()) < MIN_CLAUSE_CHARS and clauses:
            # 太短的块并回上一条：多一个"第 X 条"编号却只有三个字，通常是排版残留
            previous = clauses[-1]
            merged = body[previous.char_start:end]
            clauses[-1] = Clause(
                ordinal=previous.ordinal,
                heading=previous.heading,
                text=merged.strip(),
                kind=classify(merged),
                char_start=previous.char_start,
                char_end=end,
            )
            continue
        heading = _heading_line(chunk)
        clauses.append(
            Clause(
                ordinal=len(clauses) + 1,
                heading=heading,
                text=chunk.strip(),
                kind=classify(chunk),
                char_start=start,
                char_end=end,
            )
        )
    return clauses


def _heading_offsets(body: str) -> list[tuple[int, str]]:
    offsets: list[tuple[int, str]] = []
    cursor = 0
    for line in body.split("\n"):
        if line.strip() and any(pattern.match(line) for pattern in _HEADING_PATTERNS):
            offsets.append((cursor, line.strip()))
        cursor += len(line) + 1  # +1 是被 split 吃掉的换行
    return offsets


def _heading_line(chunk: str) -> str:
    first = chunk.strip().split("\n", 1)[0]
    return first.strip()[:200]


def classify(text: str) -> str:
    """按命中词的字符总长判类。和 P09 的 `_weight` 同一个思路：长词更专指。"""
    best_key = OTHER
    best_weight = 0
    for key, (_title, keywords) in KINDS.items():
        weight = sum(len(keyword) for keyword in keywords if keyword in text)
        if weight > best_weight:
            best_key, best_weight = key, weight
    return best_key


def title_of(kind: str) -> str:
    entry = KINDS.get(kind)
    return entry[0] if entry else OTHER_TITLE
