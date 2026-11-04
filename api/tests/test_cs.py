"""AI 电商客服（P01）测试。

分三层：
- 轨道判定是纯函数，直接钉住"什么话该转人工""什么话该拒答"；
- 服务层验证会话落库、工单不重复建；
- 最后一组是跨模块：P01 写的会话，P09 能不能真的拉去质检。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.cs import answer, faq, intents, service
from app.db.session import get_db
from app.main import app
from app.models import (
    AgentConversation,
    AgentConversationMessage,
    Base,
    CsTicket,
    QaSession,
    User,
    UserRole,
    WorkspaceMembership,
)
from app.rag.schemas import CitationRead, HitRead, SearchRead
from tests.dataplatform_support import auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/cs"


def hit(
    *,
    title: str = "低糖燕麦饼干 10 月运营复盘",
    content: str = "库存方面，12 包装 SKU 的可用量只剩 18 件，低于预警阈值 20 件。",
    matched: list[str] | None = None,
    vector: float | None = None,
) -> HitRead:
    return HitRead(
        citation=CitationRead(
            document_id=1, document_title=title, ordinal=0, char_start=0, char_end=len(content)
        ),
        content=content,
        snippet=content[:80],
        score=0.03,
        lexical_rank=1 if matched else None,
        vector_rank=1 if vector is not None else None,
        lexical_score=1.2 if matched else None,
        vector_score=vector,
        matched_terms=matched or [],
    )


# --- FAQ ---------------------------------------------------------------------


def test_faq_matches_shipping_question():
    item, hits, matched = faq.match("现货多久发货？急着用")

    assert item is not None
    assert item.key == "shipping_time"
    assert hits >= 1
    assert "多久发货" in matched


def test_faq_returns_none_for_unrelated_question():
    item, hits, matched = faq.match("今天天气怎么样")

    assert item is None
    assert hits == 0
    assert matched == []


def test_faq_ignores_single_character_keywords():
    """单字关键词误命中率太高，不该参与匹配。"""
    assert faq.MIN_KEYWORD_CHARS >= 2
    item, _hits, _matched = faq.match("券")
    assert item is None


# --- 三轨道 ------------------------------------------------------------------


def test_complaint_wins_over_explicit_request():
    """「转人工，我要投诉」的工单原因应该是投诉，不是"要求人工"。"""
    signal = intents.detect_handoff("转人工，我要投诉你们发的是假货")

    assert signal is not None
    assert signal.trigger == "complaint"
    assert signal.priority == "high"


def test_negative_emotion_routes_to_handoff():
    signal = intents.detect_handoff("问了三次都没解决，太差了")

    assert signal is not None
    assert signal.trigger in {"negative_emotion", "escalation"}


def test_plain_question_does_not_trigger_handoff():
    assert intents.detect_handoff("这个饼干保质期多久") is None


# --- 依据判定 ----------------------------------------------------------------


def test_lexical_hit_is_evidence_without_a_score_floor():
    picked, evidence, reason = answer.pick_from_hits([hit(matched=["库存", "预警"])])

    assert picked is not None
    assert evidence is not None and evidence.kind == "rag"
    assert reason == ""


def test_vector_hit_above_floor_is_evidence():
    picked, evidence, _reason = answer.pick_from_hits([hit(vector=answer.VECTOR_FLOOR + 0.05)])

    assert picked is not None
    assert evidence is not None


def test_vector_hit_below_floor_is_refused_with_the_number():
    picked, evidence, reason = answer.pick_from_hits([hit(vector=answer.VECTOR_FLOOR - 0.05)])

    assert picked is None
    assert evidence is None
    # 拒答理由要带实际相似度，不能只说"没找到"
    assert "相似度" in reason


def test_empty_hits_is_refused():
    picked, _evidence, reason = answer.pick_from_hits([])

    assert picked is None
    assert "没有可检索的内容" in reason


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
def no_rag(monkeypatch):
    """默认：知识库为空。"""
    monkeypatch.setattr(
        service.rag_service,
        "search",
        lambda *_a, **_k: SearchRead(query="", hits=[]),
    )


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_chat_answers_from_faq_and_stores_both_messages(db, tenant, no_rag):
    result = service.chat(
        db, tenant.workspace_id, user_id=tenant.user.id, message="多久发货？"
    )

    assert result.track == "faq"
    assert "48 小时" in result.reply
    assert result.citations and result.citations[0].kind == "faq"
    messages = list(
        db.scalars(
            select(AgentConversationMessage)
            .where(AgentConversationMessage.conversation_id == result.conversation_id)
            .order_by(AgentConversationMessage.id)
        )
    )
    assert [item.role for item in messages] == ["user", "assistant"]
    assert messages[1].structured_content["track"] == "faq"


def test_faq_never_touches_retrieval_even_when_it_is_broken(monkeypatch, db, tenant):
    """FAQ 是确定性知识，不该依赖检索层是否活着。

    这不是理论洁癖：检索依赖向量端点，端点抖动时 `embedding` 会熔断 30 秒。
    如果 FAQ 也走检索，那 30 秒里连"多久发货"都答不出来。
    """

    def _boom(*_args, **_kwargs):
        raise AssertionError("FAQ 轨道不应该调用检索")

    monkeypatch.setattr(service.rag_service, "search", _boom)

    result = service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="包邮吗？")

    assert result.track == "faq"
    assert "49 元包邮" in result.reply


def test_chat_answers_from_retrieval(monkeypatch, db, tenant):
    monkeypatch.setattr(
        service.rag_service,
        "search",
        lambda *_a, **_k: SearchRead(query="", hits=[hit(matched=["库存", "阈值"])]),
    )

    result = service.chat(
        db, tenant.workspace_id, user_id=tenant.user.id, message="12 包装的库存还剩多少"
    )

    assert result.track == "retrieval"
    assert "低糖燕麦饼干" in result.reply
    assert result.citations[0].label.startswith("低糖燕麦饼干")


def test_chat_refuses_when_retrieval_is_weak(monkeypatch, db, tenant):
    monkeypatch.setattr(
        service.rag_service,
        "search",
        lambda *_a, **_k: SearchRead(query="", hits=[hit(vector=0.20)]),
    )

    result = service.chat(
        db, tenant.workspace_id, user_id=tenant.user.id, message="你们公司上市了吗"
    )

    assert result.track == "refuse"
    assert "查不到可靠的依据" in result.reply
    assert result.citations == []
    assert "相似度" in result.refuse_reason


def test_complaint_creates_a_priority_ticket(db, tenant, no_rag):
    result = service.chat(
        db, tenant.workspace_id, user_id=tenant.user.id, message="我要投诉，你们发的是假货"
    )

    assert result.track == "handoff"
    assert result.handoff is not None
    assert result.handoff.trigger == "complaint"
    assert result.handoff.priority == "high"
    assert f"#{result.handoff.id}" in result.reply
    ticket = db.scalar(select(CsTicket))
    assert ticket is not None and ticket.status.value == "open"


def test_repeated_handoff_in_one_conversation_keeps_a_single_ticket(db, tenant, no_rag):
    first = service.chat(
        db, tenant.workspace_id, user_id=tenant.user.id, message="我要投诉"
    )
    second = service.chat(
        db,
        tenant.workspace_id,
        user_id=tenant.user.id,
        message="你们到底管不管，我要投诉",
        conversation_id=first.conversation_id,
    )

    assert first.handoff is not None and second.handoff is not None
    # 客户连说两句"投诉"只该有一张工单，否则人工要对着两张一样的单子问两遍
    assert first.handoff.id == second.handoff.id
    assert len(list(db.scalars(select(CsTicket)))) == 1


def test_conversation_id_is_workspace_scoped(db, tenant, no_rag):
    other = seed_tenant(db, slug="other", with_user=True)
    foreign = AgentConversation(
        workspace_id=other.workspace_id, created_by_user_id=other.user.id, title="别人的会话"
    )
    db.add(foreign)
    db.commit()

    result = service.chat(
        db,
        tenant.workspace_id,
        user_id=tenant.user.id,
        message="多久发货",
        conversation_id=foreign.id,
    )

    # 不泄露"这个 id 存在但不是你的"，直接新开一个
    assert result.conversation_id != foreign.id


def test_summary_counts_tracks_and_tickets(db, tenant, no_rag):
    service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="多久发货")
    service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="我要投诉")

    summary = service.summary(db, tenant.workspace_id)

    assert summary.conversations == 2
    assert summary.messages == 4
    assert summary.handoff_tickets == 1
    assert summary.open_tickets == 1
    assert {item.track: item.count for item in summary.tracks}["faq"] == 1
    assert {item.track: item.count for item in summary.tracks}["handoff"] == 1


def test_close_ticket_sets_closed_at(db, tenant, no_rag):
    result = service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="我要投诉")
    assert result.handoff is not None

    closed = service.close_ticket(db, tenant.workspace_id, result.handoff.id)

    assert closed is not None
    assert closed.status == "closed"
    assert closed.closed_at is not None
    # created_at 由数据库时钟写入，closed_at 也必须取同一个时钟。
    # 曾经用 Python 的 UTC 写 closed_at，MySQL 的 created_at 是本地时间（+8），
    # 两个字段一减就是 -8 小时，"关单耗时"直接变成负数。
    gap = (closed.closed_at - closed.created_at).total_seconds()
    assert -1 <= gap < 300


def test_close_ticket_from_other_workspace_is_none(db, tenant, no_rag):
    other = seed_tenant(db, slug="other", with_user=True)
    result = service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="我要投诉")
    assert result.handoff is not None

    assert service.close_ticket(db, other.workspace_id, result.handoff.id) is None


def test_chat_endpoint_returns_track_and_reason(engine, db, tenant, no_rag):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.post(f"{BASE}/chat", headers=headers, json={"message": "多久发货？"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["track"] == "faq"
    assert "FAQ 命中" in body["reason"]


def test_faq_endpoint_lists_builtin_items(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.get(f"{BASE}/faq", headers=headers)

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(response.json()) == len(faq.FAQ_ITEMS)


def test_handoff_endpoint_returns_the_ticket_it_created(engine, db, tenant, no_rag):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        chat = http.post(f"{BASE}/chat", headers=headers, json={"message": "多久发货"})
        conversation_id = chat.json()["conversation_id"]
        response = http.post(
            f"{BASE}/handoff",
            headers=headers,
            json={"conversation_id": conversation_id, "reason": "客户要求人工"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["trigger"] == "manual"
    assert response.json()["conversation_id"] == conversation_id


def test_tickets_endpoint_rejects_unknown_status(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.get(f"{BASE}/tickets", headers=headers, params={"status": "doing"})

    app.dependency_overrides.clear()

    assert response.status_code == 400


def test_viewer_can_read_but_cannot_chat(engine, db, tenant, no_rag):
    viewer = User(
        email="cs-viewer@example.com", display_name="v", role=UserRole.VIEWER, password_hash="x"
    )
    db.add(viewer)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=tenant.workspace_id, user_id=viewer.id, role=UserRole.VIEWER
        )
    )
    db.commit()

    headers, _ = auth_payload(tenant)
    with client(engine, type("Stub", (), {"id": viewer.id, "role": UserRole.VIEWER})()) as http:
        readable = http.get(f"{BASE}/tickets", headers=headers)
        forbidden = http.post(f"{BASE}/chat", headers=headers, json={"message": "多久发货"})

    app.dependency_overrides.clear()

    assert readable.status_code == 200
    assert forbidden.status_code == 403


def test_p01_conversations_flow_into_p09_assessment(db, tenant, no_rag):
    """跨模块：P01 答完的会话，P09 能真的拉去质检。

    这是矩阵里 P09 → P01 那条链路：不是造样本，是同一份数据被两个模块消费。
    """
    from app.qa import service as qa_service

    service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="多久发货？")
    service.chat(db, tenant.workspace_id, user_id=tenant.user.id, message="我要投诉，发的是假货")

    imported, skipped, scanned, reason = qa_service.import_from_agent_conversations(
        db, tenant.workspace_id
    )

    assert scanned == 2
    assert imported == 2
    assert skipped == 0
    assert reason == ""
    sessions = list(db.scalars(select(QaSession).order_by(QaSession.external_id)))
    assert len(sessions) == 2
    assert all(item.agent_name == "AI 助手" for item in sessions)
    # 会话内容原样进了质检库，评分也已经算出来了
    scored = [item.score for item in sessions]
    assert all(score is not None for score in scored)
