"""检索用的分词。

不引分词库（jieba 之类）的理由不是"省一个依赖"，是**行为不可控**：分词库的词典
决定了一次查询能不能召回，而那个词典既不在版本控制里、也不随业务词表更新。运营
同事写"ROAS 掉了一半"，词典里有没有"ROAS"不该成为检索失败的原因。

这里用一条固定规则：CJK 连续串切成**二元字组**（bigram），拉丁字母与数字按整词。
中文的 bigram 召回率接近词级分词，而它完全确定——同一段文本永远切出同样的结果，
测试可以断言。
"""

from __future__ import annotations

import re

# 只认两种片段：ASCII 字母数字串、CJK 连续串。标点与空白直接丢弃（它们是分隔符，
# 不是检索信号；标点敏感的检索需求应该走结构化过滤，不是靠分词）。
_PIECE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")

CJK_RANGE = (0x4E00, 0x9FFF)


def tokenize(text: str) -> list[str]:
    """切成检索词。bigram 对中文，整词对英文数字。"""
    tokens: list[str] = []
    for match in _PIECE.finditer(text or ""):
        piece = match.group(0)
        if piece[0].isascii():
            tokens.append(piece.lower())
            continue
        if len(piece) == 1:
            tokens.append(piece)
            continue
        # 逐字滑窗取二元组：不重叠地切会漏掉"成交额"里的"交额"
        tokens.extend(piece[index : index + 2] for index in range(len(piece) - 1))
    return tokens


def is_cjk(char: str) -> bool:
    return bool(char) and CJK_RANGE[0] <= ord(char) <= CJK_RANGE[1]
