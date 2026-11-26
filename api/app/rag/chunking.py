"""切片：按句子边界打包，保留精确字符区间。

为什么要保留 `char_start` / `char_end`：引用如果只说"来自《XX 文档》"，用户没法
核对；说"第 1280–1560 字"，他可以跳过去看一眼。这也是不把切片做成"固定 500 字
硬切"的原因——硬切会把一句话劈成两半，引用读起来像乱码。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.tokenize import tokenize

DEFAULT_TARGET = 320
DEFAULT_MAX = 480
DEFAULT_OVERLAP = 48

# 句末与段落边界。不含英文句点：`9.9 元` 会被切开，得不偿失。
_BOUNDARY = frozenset("。！？；!?;\n")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    char_start: int
    char_end: int
    token_count: int


def normalize(text: str) -> str:
    """统一换行。切片的偏移量以归一化后的文本为准，入库的原文也用这一份。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def split_text(
    text: str,
    *,
    target: int = DEFAULT_TARGET,
    max_chars: int = DEFAULT_MAX,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """把长文切成带偏移量的切片。

    `target` 是打包目标长度（尽量不超过），`max_chars` 是硬上限（单个超长句也会被
    再切），`overlap` 是相邻切片的重叠字数——重叠是为了让跨句的问题仍能召回，
    不是为了"多给点上下文"。
    """
    text = normalize(text)
    if not text.strip():
        return []

    units = _unit_spans(text, max_chars)
    if not units:
        return []

    chunks: list[Chunk] = []
    index = 0
    total = len(units)
    while index < total:
        start = units[index][0]

        # 剩下的内容一屏装得下就整段收尾。不做这一步的话，一篇 329 字的文档会被切成
        # [0,284] 和 [181,329] 两片——重复度 60%，两片在检索里互相抢名次，
        # 用户看到的是同一条结论出现两次。
        if units[total - 1][1] - start <= max_chars:
            end = units[total - 1][1]
            content = text[start:end]
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    content=content,
                    char_start=start,
                    char_end=end,
                    token_count=len(tokenize(content)),
                )
            )
            break

        end = units[index][1]
        cursor = index + 1
        while cursor < total and units[cursor][1] - start <= target:
            end = units[cursor][1]
            cursor += 1

        content = text[start:end]
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                content=content,
                char_start=start,
                char_end=end,
                token_count=len(tokenize(content)),
            )
        )

        if cursor >= total:
            break

        # 回退到"重叠至少 overlap 字"的位置；至少前进一个句子，保证一定会结束
        resume = cursor
        while resume > index + 1 and (end - units[resume][0]) < overlap:
            resume -= 1
        index = resume

    return chunks


def _unit_spans(text: str, max_chars: int) -> list[tuple[int, int]]:
    """句子级区间，超长句按 max_chars 硬切。返回的区间都已去掉首尾空白。"""
    raw: list[tuple[int, int]] = []
    start = 0
    for position, char in enumerate(text):
        if char in _BOUNDARY:
            raw.append((start, position + 1))
            start = position + 1
    if start < len(text):
        raw.append((start, len(text)))

    spans: list[tuple[int, int]] = []
    for begin, finish in raw:
        begin, finish = _trim(text, begin, finish)
        while finish - begin > max_chars:
            spans.append((begin, begin + max_chars))
            begin += max_chars
        if finish > begin:
            end, start = finish, begin
            spans.append((start, end))
    return spans


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
