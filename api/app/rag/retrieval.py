"""混合检索：BM25 + 向量，用 RRF 融合。

为什么不是"向量优先，向量没有就退化"：两路召回的错法不一样。词法错在"字面对不上
就召回不到"，向量错在"意思相近但答非所问"。融合（而不是二选一）让两边互相补位。

为什么用 RRF（Reciprocal Rank Fusion）而不是加权平均分数：BM25 的分数是无界
正数，余弦在 -1..1，两者量纲不同，加权平均需要给每个语料调一个归一化系数；
RRF 只看**名次**，`1/(k+rank)` 累加，天然免疫量纲。k=60 是论文里的常用值。

这个模块不碰数据库：输入切片集合，输出名次。这样才能不起服务就测排序逻辑。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.rag.tokenize import tokenize

RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75


@dataclass
class ChunkInput:
    """检索输入的一条切片。embedding 为 None 表示这条没向量。"""

    key: int
    content: str
    embedding: list[float] | None = None
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = tokenize(self.content)


@dataclass
class Ranked:
    key: int
    score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None
    lexical_score: float | None = None
    vector_score: float | None = None
    matched_terms: list[str] = field(default_factory=list)


def rank(
    question: str,
    chunks: list[ChunkInput],
    *,
    query_vector: list[float] | None = None,
    top_k: int = 8,
) -> list[Ranked]:
    """返回融合后的前 top_k 条。两路都没有命中就是空列表，不硬凑。"""
    if not chunks or not question.strip():
        return []

    query_tokens = tokenize(question)
    lexical = _bm25(query_tokens, chunks)
    vector = _cosine_rank(query_vector, chunks)

    fused: dict[int, Ranked] = {}
    for position, (index, score, matched) in enumerate(lexical, start=1):
        item = fused.setdefault(index, Ranked(key=chunks[index].key, score=0.0))
        item.score += 1.0 / (RRF_K + position)
        item.lexical_rank = position
        item.lexical_score = score
        item.matched_terms = matched

    for position, (index, score) in enumerate(vector, start=1):
        item = fused.setdefault(index, Ranked(key=chunks[index].key, score=0.0))
        item.score += 1.0 / (RRF_K + position)
        item.vector_rank = position
        item.vector_score = score

    # 只有一路命中时分数会明显偏低（≈1/61 对 ≈2/61），排序仍然正确：
    # 双路命中必然排在单路前面，这正是我们要的"共识优先"。
    return sorted(fused.values(), key=lambda item: item.score, reverse=True)[:top_k]


def _bm25(
    query_tokens: list[str],
    chunks: list[ChunkInput],
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> list[tuple[int, float, list[str]]]:
    total = len(chunks)
    lengths = [len(chunk.tokens) for chunk in chunks]
    average = sum(lengths) / total if total else 0.0

    # df 只统计出现的文档数；查询词去重，重复词不该被 BM25 重复计分
    terms = list(dict.fromkeys(query_tokens))
    document_frequency: dict[str, int] = {term: 0 for term in terms}
    for chunk in chunks:
        present = set(chunk.tokens)
        for term in terms:
            if term in present:
                document_frequency[term] += 1

    scored: list[tuple[int, float, list[str]]] = []
    for index, chunk in enumerate(chunks):
        counts: dict[str, int] = {}
        for token in chunk.tokens:
            counts[token] = counts.get(token, 0) + 1
        length = lengths[index] or 1
        score = 0.0
        matched: list[str] = []
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            frequency_in_docs = document_frequency[term]
            idf = math.log(1 + (total - frequency_in_docs + 0.5) / (frequency_in_docs + 0.5))
            denominator = frequency + k1 * (1 - b + b * length / (average or 1))
            score += idf * frequency * (k1 + 1) / denominator
            matched.append(term)
        if score > 0:
            scored.append((index, score, matched))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _cosine_rank(
    query_vector: list[float] | None,
    chunks: list[ChunkInput],
) -> list[tuple[int, float]]:
    if not query_vector:
        return []
    query_norm = math.sqrt(sum(value * value for value in query_vector))
    if query_norm == 0:
        return []

    scored: list[tuple[int, float]] = []
    for index, chunk in enumerate(chunks):
        if not chunk.embedding or len(chunk.embedding) != len(query_vector):
            continue
        dot = sum(a * b for a, b in zip(query_vector, chunk.embedding))
        norm = math.sqrt(sum(value * value for value in chunk.embedding))
        if norm == 0:
            continue
        similarity = dot / (query_norm * norm)
        if similarity > 0:
            scored.append((index, similarity))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored
