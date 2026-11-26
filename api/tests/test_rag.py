"""自建 RAG 测试。

分两层：
- 纯函数（分词 / 切片 / 排序）不碰数据库、不碰网络，直接把行为钉死；
- 服务与接口走 SQLite + 假向量端点，验证"没有向量也能用""引用能指回原文"
  "跨租户检不到"这三件真正会出事的地方。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, RagChunk, RagDocument, User, UserRole, WorkspaceMembership
from app.rag import chunking, retrieval, service
from app.rag.embedding import EmbedOutcome
from app.rag.tokenize import tokenize
from tests.dataplatform_support import auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/rag"

LONG_TEXT = (
    "低糖燕麦饼干的目标人群是关注控糖和代餐的上班族。"
    "卖点集中在低糖、饱腹和独立包装三点，主图需要把低糖放在第一屏。"
    "近 14 天成交额从 1200 元涨到 1700 元，但投放消耗只增加了 100 元。"
    "ROAS 从 4.2 提到 5.67，说明素材效率在提升而不是靠加预算堆出来的。"
    "库存方面，12 包装 SKU 的可用量已经低于预警阈值，需要优先补货。"
    "竞品在同价位用买一送一冲量，我们的应对方式应该是强化低糖认证而不是跟价。"
)


# --- 分词 -------------------------------------------------------------------


def test_tokenize_uses_bigrams_for_cjk():
    assert tokenize("成交额") == ["成交", "交额"]
    # 单字保留，标点与空白丢弃
    assert tokenize("额。") == ["额"]


def test_tokenize_keeps_latin_words_whole_and_lowercased():
    assert tokenize("ROAS 掉了一半") == ["roas", "掉了", "了一", "一半"]


# --- 切片 -------------------------------------------------------------------


def test_split_text_offsets_point_into_original_text():
    pieces = chunking.split_text(LONG_TEXT, target=80, max_chars=120, overlap=16)

    assert len(pieces) >= 3
    assert [piece.ordinal for piece in pieces] == list(range(len(pieces)))
    for piece in pieces:
        # 这是"引用能核对"的底层保证
        assert LONG_TEXT[piece.char_start : piece.char_end] == piece.content
        assert len(piece.content) <= 120
        assert piece.token_count > 0


def test_adjacent_chunks_overlap():
    # 用等长句子构造，重叠量才可断言；真实文本的句长不均，"至少重叠 N 字"是尽力而为
    sentence = "运营复盘要写清口径"
    text = "。".join([sentence] * 30) + "。"

    pieces = chunking.split_text(text, target=30, max_chars=40, overlap=9)

    assert len(pieces) > 5
    for previous, current in zip(pieces, pieces[1:]):
        assert current.char_start < previous.char_end
        assert previous.char_end - current.char_start >= 9


def test_split_text_cuts_single_long_sentence():
    text = "很长的句子" * 400  # 2000 字，没有任何句末标点

    pieces = chunking.split_text(text, target=320, max_chars=480)

    assert len(pieces) >= 4
    assert all(len(piece.content) <= 480 for piece in pieces)


def test_split_text_returns_nothing_for_blank_input():
    assert chunking.split_text("   \n\n ") == []


# --- 排序 -------------------------------------------------------------------


def test_bm25_ranks_matching_chunk_first():
    chunks = [
        retrieval.ChunkInput(key=1, content="库存预警：12 包装 SKU 可用量不足"),
        retrieval.ChunkInput(key=2, content="竞品用买一送一冲量"),
    ]

    ranked = retrieval.rank("库存预警", chunks, top_k=2)

    assert ranked[0].key == 1
    assert ranked[0].lexical_rank == 1
    assert "库存" in ranked[0].matched_terms


def test_vector_recall_finds_what_lexical_misses():
    chunks = [
        retrieval.ChunkInput(key=1, content="成交额 1700 元", embedding=[1.0, 0.0]),
        retrieval.ChunkInput(key=2, content="投放效率明显改善", embedding=[1.0, 0.0]),
    ]

    lexical_only = retrieval.rank("成交额", chunks, top_k=5)
    assert [item.key for item in lexical_only] == [1]

    # 第二条字面上完全不含"成交额"，只能靠向量召回
    with_vector = retrieval.rank("成交额", chunks, query_vector=[1.0, 0.0], top_k=5)
    assert {item.key for item in with_vector} == {1, 2}
    # 两路都命中的排在只有一路的前面
    assert with_vector[0].key == 1


# --- 服务与接口 --------------------------------------------------------------


@pytest.fixture()
def engine():
    return make_engine()


@pytest.fixture()
def db(engine):
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def tenant(db):
    return seed_tenant(db, with_user=True)


@pytest.fixture()
def no_vectors(monkeypatch):
    """默认场景：向量端点没配。这条路径必须完全可用。"""
    monkeypatch.setattr(service.embedding, "is_configured", lambda *a, **k: False)
    monkeypatch.setattr(
        service.embedding,
        "embed_texts",
        lambda texts, **kwargs: EmbedOutcome(error="未配置 EMBEDDING_MODEL"),
    )


@pytest.fixture()
def fake_vectors(monkeypatch):
    """假向量端：含"成交额"的切片拿 [1,0]，其余拿 [0,1]。"""

    def _embed(texts, **_kwargs):
        vectors = [[1.0, 0.0] if "成交额" in text else [0.0, 1.0] for text in texts]
        return EmbedOutcome(vectors=vectors, model="fake-embed", batches=1)

    monkeypatch.setattr(service.embedding, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(service.embedding, "embed_texts", _embed)


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_ingest_stores_original_and_chunks_without_vectors(db, tenant, no_vectors):
    result = service.ingest(
        db, tenant.workspace_id, title="低糖饼干运营复盘", content=LONG_TEXT
    )

    assert result.document.chunk_count >= 1
    assert result.document.embedded_chunks == 0
    assert result.document.char_count == len(LONG_TEXT)
    # 降级要如实说出来，不能假装向量化成功
    assert result.warnings and "词法" in result.warnings[0]

    stored = db.get(RagDocument, result.document.id)
    assert stored is not None
    assert stored.content == LONG_TEXT


def test_ingest_with_vectors_marks_coverage(db, tenant, fake_vectors):
    result = service.ingest(db, tenant.workspace_id, title="带向量", content=LONG_TEXT)

    assert result.document.embedded_chunks == result.document.chunk_count
    assert result.document.embedding_model == "fake-embed"
    assert result.warnings == []

    stats = service.stats(db, tenant.workspace_id)
    assert stats.embedding_coverage == 1.0


def test_search_citation_slices_back_to_original(db, tenant, no_vectors):
    service.ingest(db, tenant.workspace_id, title="低糖饼干运营复盘", content=LONG_TEXT)

    result = service.search(db, tenant.workspace_id, "库存预警", top_k=3)

    assert result.hits
    hit = result.hits[0]
    document = db.get(RagDocument, hit.citation.document_id)
    assert document is not None
    # 引用不是装饰：拿区间回原文能切出一模一样的内容
    assert document.content[hit.citation.char_start : hit.citation.char_end] == hit.content
    assert hit.citation.document_title == "低糖饼干运营复盘"


def test_search_without_vectors_reports_lexical_only(db, tenant, no_vectors):
    service.ingest(db, tenant.workspace_id, title="复盘", content=LONG_TEXT)

    result = service.search(db, tenant.workspace_id, "成交额", top_k=3)

    assert result.vector_enabled is False
    assert result.vector_hits == 0
    assert any("EMBEDDING_MODEL" in warning for warning in result.warnings)


def test_search_is_workspace_scoped(db, tenant, no_vectors):
    other = seed_tenant(db, slug="other")
    service.ingest(db, tenant.workspace_id, title="A 的复盘", content=LONG_TEXT)

    result = service.search(db, other.workspace_id, "成交额", top_k=3)

    assert result.hits == []


def test_delete_document_also_removes_chunks(db, tenant, no_vectors):
    created = service.ingest(db, tenant.workspace_id, title="待删", content=LONG_TEXT)
    assert db.scalar(select(func.count()).select_from(RagChunk)) > 0

    assert service.delete_document(db, tenant.workspace_id, created.document.id) is True
    assert db.scalar(select(func.count()).select_from(RagChunk)) == 0


def test_delete_from_other_tenant_returns_false(db, tenant, no_vectors):
    other = seed_tenant(db, slug="other")
    created = service.ingest(db, tenant.workspace_id, title="A 的复盘", content=LONG_TEXT)

    assert service.delete_document(db, other.workspace_id, created.document.id) is False


def test_ingest_endpoint_then_search(engine, tenant, no_vectors):
    headers, user = auth_payload(tenant)

    with client(engine, user) as http:
        created = http.post(
            f"{BASE}/documents",
            headers=headers,
            json={"title": "低糖饼干运营复盘", "content": LONG_TEXT},
        )
        documents = http.get(f"{BASE}/documents", headers=headers)
        search = http.post(f"{BASE}/search", headers=headers, json={"query": "ROAS"})
        stats = http.get(f"{BASE}/stats", headers=headers)

    app.dependency_overrides.clear()

    assert created.status_code == 201
    assert created.json()["document"]["chunk_count"] >= 1
    assert len(documents.json()) == 1
    assert search.status_code == 200
    payload = search.json()
    assert payload["hits"]
    assert payload["hits"][0]["citation"]["char_end"] > payload["hits"][0]["citation"]["char_start"]
    assert stats.json()["documents"] == 1


def test_search_before_any_document_returns_empty(engine, tenant, no_vectors):
    headers, user = auth_payload(tenant)

    with client(engine, user) as http:
        response = http.post(f"{BASE}/search", headers=headers, json={"query": "成交额"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["hits"] == []


def test_delete_endpoint_reports_404_for_unknown_document(engine, tenant, no_vectors):
    headers, user = auth_payload(tenant)

    with client(engine, user) as http:
        response = http.delete(f"{BASE}/documents/999", headers=headers)

    app.dependency_overrides.clear()
    assert response.status_code == 404


def test_viewer_can_search_but_cannot_ingest(engine, tenant, db, no_vectors):
    viewer = User(email="rag-viewer@example.com", display_name="v", role=UserRole.VIEWER, password_hash="x")
    db.add(viewer)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=tenant.workspace_id,
            user_id=viewer.id,
            role=UserRole.VIEWER,
        )
    )
    db.commit()

    headers, _ = auth_payload(tenant)
    with client(engine, type("Stub", (), {"id": viewer.id, "role": UserRole.VIEWER})()) as http:
        readable = http.post(f"{BASE}/search", headers=headers, json={"query": "成交额"})
        forbidden = http.post(
            f"{BASE}/documents", headers=headers, json={"title": "x", "content": "内容"}
        )

    app.dependency_overrides.clear()

    assert readable.status_code == 200
    assert forbidden.status_code == 403


def test_tail_is_not_split_into_near_duplicate_chunk():
    """一篇比 target 略长的文档必须只剩一片。

    回归点：target 是软上限，不是硬切点。曾经一篇 329 字的复盘被切成 [0,284] 和
    [181,329] 两片，重复 103 字；两片在检索里互相抢名次，同一条结论会重复出现。
    """
    text = "运营复盘。" * 66  # 330 字，超过默认 target=320

    pieces = chunking.split_text(text)

    assert len(pieces) == 1
    assert (pieces[0].char_start, pieces[0].char_end) == (0, len(text))


class _TimeoutClient:
    """模拟连接阶段超时：真实发生过一次，代价是检索卡 21 秒。"""

    attempts = 0

    def __init__(self, **_kwargs):
        type(self).attempts += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, *_args, **_kwargs):
        import httpx

        raise httpx.ConnectTimeout("handshake timed out")


class _OkClient:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, *_args, **_kwargs):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}

        return _Response()


def test_embedding_timeouts_are_short_enough_for_an_interactive_request():
    """超时必须"短到用户还愿意等"：向量是增强项，宁可降级也不要拖住检索。"""
    from app.rag import embedding

    assert embedding.TIMEOUT_SECONDS <= 10
    assert embedding.CONNECT_TIMEOUT_SECONDS <= 3
    assert embedding.CONNECT_TIMEOUT_SECONDS < embedding.TIMEOUT_SECONDS


def test_embedding_failure_opens_breaker_and_skips_the_next_attempt(monkeypatch):
    """一次抖动之后不要每次都重新吃一遍超时——否则整段时间的检索都跟着慢。"""
    from app.rag import embedding

    monkeypatch.setattr(embedding, "resolve", lambda *a, **k: ("http://fake", "key", "fake-model"))
    monkeypatch.setattr(embedding.httpx, "Client", _TimeoutClient)
    embedding.reset_breaker()
    _TimeoutClient.attempts = 0

    first = embedding.embed_texts(["缺货要不要紧"])
    second = embedding.embed_texts(["竞品价格怎么应对"])

    assert first.vectors is None and "ConnectTimeout" in (first.error or "")
    assert second.vectors is None and "跳过" in (second.error or "")
    # 第二次根本不该再建连接
    assert _TimeoutClient.attempts == 1
    assert embedding.breaker_wait() > 0

    embedding.reset_breaker()
    assert embedding.breaker_wait() == 0


def test_embedding_success_clears_breaker(monkeypatch):
    """恢复后必须自动重试，不能因为一次失败就永久关掉向量。"""
    from app.rag import embedding

    monkeypatch.setattr(embedding, "resolve", lambda *a, **k: ("http://fake", "key", "fake-model"))
    monkeypatch.setattr(embedding.httpx, "Client", _TimeoutClient)
    embedding.reset_breaker()
    assert embedding.embed_texts(["缺货要不要紧"]).vectors is None

    monkeypatch.setattr(embedding.httpx, "Client", _OkClient)
    embedding.reset_breaker()
    outcome = embedding.embed_texts(["缺货要不要紧"])

    assert outcome.vectors == [[0.1, 0.2]]
    assert embedding.breaker_wait() == 0
    assert embedding.breaker_reason() is None
