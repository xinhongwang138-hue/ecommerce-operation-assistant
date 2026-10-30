"""RAG 服务层：入库、检索、统计。

入库是"先落原文与切片，再尽力补向量"的顺序，不是"先向量化，成功才入库"。理由是
失败的位置决定失败的代价：向量端点抽风时，前者只损失召回质量（词法仍然可用），
后者让整篇文档白传一次。
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RagChunk, RagDocument, RagSourceKind
from app.rag import chunking, embedding, retrieval
from app.rag.tokenize import tokenize
from app.rag.schemas import (
    CitationRead,
    DocumentRead,
    HitRead,
    IngestRead,
    SearchRead,
    StatsRead,
)

# 单次检索最多扫这么多切片。超过就该上向量库/倒排索引（ADR 0009），
# 而不是在这条 SQL 上加条件假装很快。
MAX_SCAN_CHUNKS = 5000

SNIPPET_CHARS = 220


def ingest(
    db: Session,
    workspace_id: int,
    *,
    title: str,
    content: str,
    source_kind: str = "text",
    created_by_id: int | None = None,
) -> IngestRead:
    normalized = chunking.normalize(content)
    pieces = chunking.split_text(normalized)
    if not pieces:
        raise ValueError("内容里没有可切片的文本")

    document = RagDocument(
        workspace_id=workspace_id,
        created_by_id=created_by_id,
        title=title.strip(),
        source_kind=RagSourceKind(source_kind),
        content=normalized,
        char_count=len(normalized),
        chunk_count=len(pieces),
    )
    db.add(document)
    db.flush()

    for piece in pieces:
        db.add(
            RagChunk(
                workspace_id=workspace_id,
                document_id=document.id,
                ordinal=piece.ordinal,
                content=piece.content,
                char_start=piece.char_start,
                char_end=piece.char_end,
                token_count=piece.token_count,
            )
        )
    db.flush()

    warnings: list[str] = []
    outcome = embedding.embed_texts([piece.content for piece in pieces])
    if outcome.vectors:
        stored = (
            db.query(RagChunk)
            .filter(RagChunk.document_id == document.id)
            .order_by(RagChunk.ordinal)
            .all()
        )
        for row, vector in zip(stored, outcome.vectors):
            row.embedding = json.dumps(vector)
        document.embedding_model = outcome.model
        document.embedded_chunks = len(outcome.vectors)
    else:
        warnings.append(
            f"未生成向量（{outcome.error}），本次入库只支持词法检索；"
            "配置 EMBEDDING_MODEL 后重新入库即可补齐"
        )

    db.commit()
    db.refresh(document)
    return IngestRead(document=_document_read(document), warnings=warnings)


def list_documents(db: Session, workspace_id: int, *, limit: int = 50) -> list[DocumentRead]:
    rows = db.scalars(
        select(RagDocument)
        .where(RagDocument.workspace_id == workspace_id)
        .order_by(RagDocument.id.desc())
        .limit(limit)
    ).all()
    return [_document_read(row) for row in rows]


def delete_document(db: Session, workspace_id: int, document_id: int) -> bool:
    document = db.scalars(
        select(RagDocument).where(
            RagDocument.id == document_id,
            RagDocument.workspace_id == workspace_id,
        )
    ).first()
    if document is None:
        return False
    # 切片走 ORM 级联删除（cascade="all, delete-orphan"），不依赖数据库外键的
    # ON DELETE CASCADE：SQLite 默认不开外键约束，靠库会漏删。
    db.delete(document)
    db.commit()
    return True


def stats(db: Session, workspace_id: int) -> StatsRead:
    documents = db.scalar(
        select(func.count()).select_from(RagDocument).where(RagDocument.workspace_id == workspace_id)
    ) or 0
    chunks = db.scalar(
        select(func.count()).select_from(RagChunk).where(RagChunk.workspace_id == workspace_id)
    ) or 0
    embedded = db.scalar(
        select(func.count())
        .select_from(RagChunk)
        .where(RagChunk.workspace_id == workspace_id, RagChunk.embedding.is_not(None))
    ) or 0
    return StatsRead(
        documents=int(documents),
        chunks=int(chunks),
        embedded_chunks=int(embedded),
        embedding_coverage=round(embedded / chunks, 4) if chunks else 0.0,
        embedding_configured=embedding.is_configured(),
        embedding_model=embedding.model_name() if embedding.is_configured() else None,
    )


def search(db: Session, workspace_id: int, query: str, *, top_k: int = 6) -> SearchRead:
    started = time.perf_counter()
    warnings: list[str] = []

    rows = _load_chunks(db, workspace_id, warnings)
    if not rows:
        return SearchRead(query=query, warnings=warnings, elapsed_ms=_elapsed(started))

    inputs = [
        retrieval.ChunkInput(
            key=index,
            content=row.content,
            embedding=_parse_embedding(row.embedding),
        )
        for index, row in enumerate(rows)
    ]

    query_vector: list[float] | None = None
    if embedding.is_configured():
        outcome = embedding.embed_texts([query])
        if outcome.vectors:
            query_vector = outcome.vectors[0]
        else:
            warnings.append(f"本次未做向量召回（{outcome.error}），结果仅来自词法")
    else:
        warnings.append("未配置 EMBEDDING_MODEL，本次结果仅来自词法召回")

    ranked = retrieval.rank(query, inputs, query_vector=query_vector, top_k=top_k)
    hits = [
        _hit(rows[item.key], item, query)
        for item in ranked
    ]
    return SearchRead(
        query=query,
        hits=hits,
        lexical_hits=sum(1 for item in ranked if item.lexical_rank is not None),
        vector_hits=sum(1 for item in ranked if item.vector_rank is not None),
        vector_enabled=query_vector is not None,
        warnings=warnings,
        elapsed_ms=_elapsed(started),
    )


def _load_chunks(db: Session, workspace_id: int, warnings: list[str]) -> list[Any]:
    total = db.scalar(
        select(func.count()).select_from(RagChunk).where(RagChunk.workspace_id == workspace_id)
    ) or 0
    if total > MAX_SCAN_CHUNKS:
        warnings.append(
            f"切片总数 {total} 超过单次扫描上限 {MAX_SCAN_CHUNKS}，本次只扫描了最新的 "
            f"{MAX_SCAN_CHUNKS} 条；该换向量库了（见 ADR 0009）"
        )
    return db.execute(
        select(
            RagChunk.id,
            RagChunk.document_id,
            RagChunk.ordinal,
            RagChunk.content,
            RagChunk.char_start,
            RagChunk.char_end,
            RagChunk.embedding,
            RagDocument.title,
        )
        .join(RagDocument, RagDocument.id == RagChunk.document_id)
        .where(RagChunk.workspace_id == workspace_id)
        .order_by(RagChunk.id.desc())
        .limit(MAX_SCAN_CHUNKS)
    ).all()


def _hit(row: Any, ranked: retrieval.Ranked, query: str) -> HitRead:
    return HitRead(
        citation=CitationRead(
            document_id=row.document_id,
            document_title=row.title,
            ordinal=row.ordinal,
            char_start=row.char_start,
            char_end=row.char_end,
        ),
        content=row.content,
        snippet=_snippet(row.content, query),
        score=round(ranked.score, 6),
        lexical_rank=ranked.lexical_rank,
        vector_rank=ranked.vector_rank,
        lexical_score=round(ranked.lexical_score, 4) if ranked.lexical_score is not None else None,
        vector_score=round(ranked.vector_score, 4) if ranked.vector_score is not None else None,
        matched_terms=ranked.matched_terms[:6],
    )


def _snippet(content: str, query: str) -> str:
    """截一段能看懂的话。命中词存在时把窗口挪到它附近，而不是永远取开头。"""
    if len(content) <= SNIPPET_CHARS:
        return content
    position = -1
    for token in tokenize(query):
        position = content.find(token)
        if position > 0:
            break
    start = 0 if position <= 0 else max(0, position - SNIPPET_CHARS // 3)
    end = min(len(content), start + SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def _parse_embedding(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def _document_read(document: RagDocument) -> DocumentRead:
    return DocumentRead(
        id=document.id,
        title=document.title,
        source_kind=document.source_kind.value
        if isinstance(document.source_kind, RagSourceKind)
        else str(document.source_kind),
        char_count=document.char_count,
        chunk_count=document.chunk_count,
        embedded_chunks=document.embedded_chunks,
        embedding_model=document.embedding_model,
        created_at=document.created_at,
    )


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
