"""销售考核（P09）测试。

分两层：
- 规则引擎是纯函数，直接钉住"什么算扣分、扣多少、证据指哪句话"；
- 服务与接口走 SQLite，验证幂等、租户隔离、导出过滤这些真会出事的地方。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    AgentConversation,
    AgentConversationMessage,
    Base,
    QaScore,
    QaSession,
    User,
    UserRole,
    WorkspaceMembership,
)
from app.qa import rules, service
from app.qa.schemas import ImportRequest
from tests.dataplatform_support import auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/qa"
# 会话固定落在"昨天"，这样 30 天窗口在任何执行时刻都成立
BASE_TIME = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)


def U(seq: int, role: str, content: str, offset: int | None = None) -> rules.Utterance:
    return rules.Utterance(
        seq=seq,
        role=role,
        content=content,
        sent_at=None if offset is None else BASE_TIME + timedelta(seconds=offset),
    )


GOOD_SESSION = [
    U(0, "customer", "这个低糖饼干还有货吗，多少钱", 0),
    U(1, "agent", "您好，有的，29.9 元一盒，可以帮您留货，稍后发链接给您", 25),
    U(2, "customer", "好的谢谢", 40),
    U(3, "agent", "不客气，感谢您的支持，还有其他可以帮您吗", 55),
]


# --- 规则引擎 ---------------------------------------------------------------


def test_perfect_session_scores_full_marks():
    card = rules.score_session(GOOD_SESSION)

    assert card.total == 100.0
    assert card.grade == "A"
    assert card.redline is False
    assert [item.key for item in card.breakdown] == [
        "completeness",
        "first_response",
        "requirement_hit",
        "next_action",
        "courtesy",
    ]


def test_unanswered_customer_message_costs_completeness():
    messages = [
        U(0, "customer", "在吗", 0),
        U(1, "customer", "想问下什么时候发货", 30),
    ]

    result = rules.score_session(messages).breakdown[0]

    assert result.key == "completeness"
    assert result.score == 0.0
    assert "2 条客户发言中 2 条没有得到回复" in result.detail
    assert result.evidence  # 扣分必须能指回原句


def test_first_response_is_zero_when_timestamps_are_missing():
    """缺时间戳时判 0 并说明，不猜一个"大概挺快"。"""
    messages = [
        U(0, "customer", "有货吗", None),
        U(1, "agent", "有货的，我给您留一件", None),
    ]

    result = rules.score_session(messages).breakdown[1]

    assert result.key == "first_response"
    assert result.score == 0.0
    assert "缺少消息时间戳" in result.detail


def test_first_response_decays_between_the_two_thresholds():
    slow = [
        U(0, "customer", "有货吗", 0),
        U(1, "agent", "有的，帮您留一件", 180),
    ]

    result = rules.score_session(slow).breakdown[1]

    # 60 秒满分、300 秒零分，180 秒正好在一半
    assert result.score == pytest.approx(10.0, abs=0.1)
    assert result.passed is False


def test_requirement_hit_counts_intents_not_literal_words():
    """回归点：客户问「还有货吗，多少钱」，坐席答「有的，29.9 元」。

    字面交集是 0（回复没重复商品名），但人看着完全答到了。早先按关键词交集判分，
    这条会话被误判 0 分；改成"问题类型覆盖"后判满分。
    """
    result = rules.score_session(GOOD_SESSION).breakdown[2]

    assert result.key == "requirement_hit"
    assert result.score == 25.0
    assert "价格" in result.detail and "库存" in result.detail


def test_requirement_hit_flags_an_unanswered_question_type():
    messages = [
        U(0, "customer", "这个多少钱，能便宜点吗", 0),
        U(1, "agent", "您好，这款是我们的新品", 10),
    ]

    result = rules.score_session(messages).breakdown[2]

    assert result.score == 0.0
    assert "价格" in result.advice


def test_next_action_needs_an_actual_action_phrase():
    messages = [
        U(0, "customer", "有货吗", 0),
        U(1, "agent", "您好，有的", 10),
    ]

    result = rules.score_session(messages).breakdown[3]

    assert result.key == "next_action"
    assert result.score == 0.0


def test_courtesy_splits_opening_and_closing():
    messages = [
        U(0, "customer", "有货吗", 0),
        U(1, "agent", "有的，帮您留一件", 10),
    ]

    result = rules.score_session(messages).breakdown[4]

    assert result.score == 0.0
    assert "开场问候" in result.detail and "结束致谢" in result.detail


def test_redline_zeroes_the_total_but_keeps_breakdown():
    messages = [
        U(0, "customer", "真的有效吗", 0),
        U(1, "agent", "百分百保证有效，绝对没问题", 10),
    ]

    card = rules.score_session(messages)

    assert card.total == 0.0
    assert card.grade == "D"
    assert card.redline is True
    assert card.redline_rules == ["absolute_promise"]
    # 明细仍要保留：报告需要说明"本来能拿多少分"
    assert len(card.breakdown) == 5


def test_redline_ignores_what_the_customer_says():
    """客户骂人不算坐席的错。红线只看坐席说的话。"""
    messages = [
        U(0, "customer", "你们就是骗子，滚蛋", 0),
        U(1, "agent", "您好，非常抱歉让您不满意，我马上帮您处理退款", 10),
    ]

    card = rules.score_session(messages)

    assert card.redline is False
    assert card.redline_rules == []


def test_redline_skips_phrase_swallowed_by_a_benign_compound():
    """「回滚」不是骂人「滚」。

    回归点：真实运营复盘原文写着「第二版主图回滚」，规则按 `"滚" in text` 判成
    坐席失礼，三条 P01 会话全部归零。红线一票否决，误判的代价是把正常会话作废。
    """
    messages = [
        U(0, "customer", "缺货要不要紧", 0),
        U(1, "agent", "结论是库存偏低，下一步把第二版主图回滚，预算向第一版倾斜", 10),
    ]

    card = rules.score_session(messages)

    assert card.redline is False
    assert card.redline_rules == []


def test_redline_still_fires_on_real_rudeness():
    """排除正常词不能顺手把真骂人一起放掉。"""
    messages = [
        U(0, "customer", "问个问题", 0),
        U(1, "agent", "自己看详情页，别问了", 10),
    ]

    card = rules.score_session(messages)

    assert card.redline is True
    assert card.redline_rules == ["rudeness"]


def test_seven_day_no_reason_return_is_not_an_absolute_promise():
    """「7 天无理由退换」是法定权利，不是绝对化承诺。

    这条尤其要紧：FAQ 的标准答案里就写着「支持 7 天无理由退换」，一旦被误判成
    红线，客服每答一次退货政策就把自己的会话打成 0 分。
    """
    messages = [
        U(0, "customer", "能退吗", 0),
        U(1, "agent", "您好，支持 7 天无理由退换，食品类需未拆封，我帮您发起申请", 10),
    ]

    card = rules.score_session(messages)

    assert card.redline is False
    assert card.redline_rules == []


@pytest.mark.parametrize(
    ("total", "grade"),
    [(100.0, "A"), (85.0, "A"), (84.9, "B"), (70.0, "B"), (69.9, "C"), (60.0, "C"), (59.9, "D")],
)
def test_grade_boundaries(total, grade):
    assert rules.grade_of(total) == grade


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


def _session_payload(
    external_id: str = "s-1",
    agent_name: str = "小王",
    messages: list[tuple[str, str, int]] | None = None,
) -> dict:
    turns = messages or [
        ("user", "这个低糖饼干还有货吗，多少钱", 0),
        ("bot", "您好，有的，29.9 元一盒，可以帮您留货，稍后发链接给您", 25),
        ("user", "好的谢谢", 40),
        ("bot", "不客气，感谢您的支持，还有其他可以帮您吗", 55),
    ]
    return {
        "external_id": external_id,
        "agent_name": agent_name,
        "channel": "douyin",
        "started_at": BASE_TIME.isoformat(),
        "messages": [
            {"role": role, "content": content, "sent_at": (BASE_TIME + timedelta(seconds=offset)).isoformat()}
            for role, content, offset in turns
        ],
    }


def _import(db, workspace_id: int, *sessions: dict):
    return service.import_sessions(db, workspace_id, ImportRequest(sessions=list(sessions)))


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_import_scores_immediately_and_is_idempotent(db, tenant):
    first = _import(db, tenant.workspace_id, _session_payload())
    second = _import(db, tenant.workspace_id, _session_payload())

    assert (first.imported, first.updated, first.scored) == (1, 0, 1)
    # 重复导入只能更新：分母一旦能被反复导大，这个排行榜就没人信
    assert (second.imported, second.updated) == (0, 1)
    assert db.scalar(select(QaSession.id).where(QaSession.workspace_id == tenant.workspace_id))
    assert len(list(db.scalars(select(QaSession)))) == 1
    assert db.scalar(select(QaScore.total)) is not None


def test_import_maps_upstream_role_names(db, tenant):
    payload = _session_payload(
        messages=[
            ("customer", "有货吗", 0),
            ("assistant", "您好，有的，帮您留一件", 20),
        ]
    )

    _import(db, tenant.workspace_id, payload)

    from app.models import QaMessage

    roles = [item.role.value for item in db.scalars(select(QaMessage).order_by(QaMessage.seq))]
    assert roles == ["customer", "agent"]


def test_import_skips_session_without_customer_messages(db, tenant):
    payload = _session_payload(messages=[("bot", "您好，在的", 0)])

    result = _import(db, tenant.workspace_id, payload)

    assert result.imported == 0 and result.skipped == 1
    assert any("没有客户发言" in warning for warning in result.warnings)
    assert db.scalar(select(QaSession.id)) is None


def test_import_skips_session_without_any_timestamp(db, tenant):
    payload = {
        "external_id": "s-no-time",
        "agent_name": "小王",
        "messages": [
            {"role": "user", "content": "有货吗"},
            {"role": "bot", "content": "有的"},
        ],
    }

    result = _import(db, tenant.workspace_id, payload)

    assert result.skipped == 1
    assert any("无法归入周期" in warning for warning in result.warnings)


def test_rescore_restores_scores_after_manual_tampering(db, tenant):
    """重算是显式动作：分数被改动后，只有调它才会回到规则算出的值。"""
    _import(db, tenant.workspace_id, _session_payload())
    record = db.scalar(select(QaScore))
    record.total = 1.0
    record.grade = "D"
    db.commit()

    count = service.rescore(db, tenant.workspace_id)

    db.refresh(record)
    assert count == 1
    assert float(record.total) == 100.0
    assert record.grade == "A"


def test_leaderboard_aggregates_by_agent(db, tenant):
    _import(
        db,
        tenant.workspace_id,
        _session_payload("s-a1", "小王"),
        _session_payload("s-a2", "小王"),
        _session_payload(
            "s-b1",
            "小李",
            messages=[
                ("user", "多少钱", 0),
                ("bot", "嗯", 600),
            ],
        ),
    )

    board = service.leaderboard(db, tenant.workspace_id, days=30)

    assert [row.agent_name for row in board.rows] == ["小王", "小李"]
    assert board.rows[0].average == 100.0
    assert board.rows[0].sessions == 2
    assert board.rows[0].a_count == 2
    assert board.rows[1].average < 60
    assert board.total_sessions == 3


def test_leaderboard_is_workspace_scoped(db, tenant):
    other = seed_tenant(db, slug="other", with_user=True)
    _import(db, tenant.workspace_id, _session_payload("s-1", "小王"))
    _import(db, other.workspace_id, _session_payload("s-1", "别人"))

    board = service.leaderboard(db, tenant.workspace_id, days=30)

    assert [row.agent_name for row in board.rows] == ["小王"]


def test_report_returns_evidence_and_the_weakest_item(db, tenant):
    result = _import(
        db,
        tenant.workspace_id,
        _session_payload(
            "s-weak",
            "小王",
            messages=[
                ("user", "多少钱，能便宜点吗", 0),
                ("bot", "嗯", 900),
            ],
        ),
    )
    session_id = db.scalar(select(QaSession.id).where(QaSession.external_id == "s-weak"))

    report = service.get_report(db, tenant.workspace_id, session_id)

    assert result.imported == 1
    assert report is not None
    assert report.session.total is not None and report.session.total < 60
    assert report.weakest is not None
    assert report.weakest.max_score - report.weakest.score >= 15
    assert report.suggestions  # 报告必须给出"接下来改什么"
    assert len(report.messages) == 2


def test_report_returns_none_for_other_workspace(db, tenant):
    other = seed_tenant(db, slug="other", with_user=True)
    _import(db, tenant.workspace_id, _session_payload())
    session_id = db.scalar(select(QaSession.id))

    assert service.get_report(db, other.workspace_id, session_id) is None


def test_export_low_scores_filters_and_keeps_original_text(db, tenant):
    _import(db, tenant.workspace_id, _session_payload("s-good", "小王"))
    _import(
        db,
        tenant.workspace_id,
        _session_payload(
            "s-bad",
            "小李",
            messages=[("user", "多少钱", 0), ("bot", "嗯", 600)],
        ),
    )

    export = service.export_low_scores(db, tenant.workspace_id, threshold=60.0)

    assert [item.external_id for item in export.items] == ["s-bad"]
    item = export.items[0]
    assert item.total < 60
    # 回流成训练数据要原文，不是只有分数
    assert [message.role for message in item.messages] == ["customer", "agent"]
    assert item.issues  # 只带没通过的项，报告才有落点
    assert all(not issue.passed for issue in item.issues)


def test_export_excludes_everything_when_threshold_is_zero(db, tenant):
    _import(db, tenant.workspace_id, _session_payload())

    export = service.export_low_scores(db, tenant.workspace_id, threshold=0.0)

    assert export.items == []


def test_import_from_agent_conversations_maps_roles(db, tenant):
    conversation = AgentConversation(
        workspace_id=tenant.workspace_id,
        created_by_user_id=tenant.user.id,
        title="低糖饼干咨询",
    )
    db.add(conversation)
    db.flush()
    for role, content in (
        ("user", "这个低糖饼干还有货吗，多少钱"),
        ("assistant", "您好，有的，29.9 元一盒，可以帮您留货，稍后发链接给您"),
    ):
        db.add(
            AgentConversationMessage(
                conversation_id=conversation.id,
                workspace_id=tenant.workspace_id,
                role=role,
                content=content,
            )
        )
    db.commit()

    imported, skipped, scanned, reason = service.import_from_agent_conversations(
        db, tenant.workspace_id
    )

    assert (imported, skipped, scanned) == (1, 0, 1)
    assert reason == ""
    session = db.scalar(select(QaSession).where(QaSession.external_id == f"agent-{conversation.id}"))
    assert session is not None
    assert session.agent_name == "AI 助手"
    assert session.source_kind.value == "agent"


def test_import_from_agent_conversations_without_data_returns_zero(db, tenant):
    imported, skipped, scanned, reason = service.import_from_agent_conversations(
        db, tenant.workspace_id
    )

    assert (imported, skipped, scanned) == (0, 0, 0)
    assert "没有客服会话" in reason


def test_import_endpoint_then_summary(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        created = http.post(
            f"{BASE}/import", headers=headers, json={"sessions": [_session_payload()]}
        )
        summary = http.get(f"{BASE}/summary", headers=headers, params={"days": 30})
        board = http.get(f"{BASE}/leaderboard", headers=headers, params={"days": 30})

    app.dependency_overrides.clear()

    assert created.status_code == 201
    assert created.json()["imported"] == 1
    body = summary.json()
    assert body["sessions"] == 1 and body["scored"] == 1
    assert body["grade_distribution"]["A"] == 1
    assert body["rule_version"] == rules.RULE_VERSION
    assert board.json()["rows"][0]["agent_name"] == "小王"


def test_report_endpoint_returns_404_for_unknown_session(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.get(f"{BASE}/sessions/999999", headers=headers)

    app.dependency_overrides.clear()

    assert response.status_code == 404


def test_viewer_can_read_but_cannot_import(engine, db, tenant):
    viewer = User(
        email="qa-viewer@example.com", display_name="v", role=UserRole.VIEWER, password_hash="x"
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
        readable = http.get(f"{BASE}/summary", headers=headers)
        forbidden = http.post(
            f"{BASE}/import", headers=headers, json={"sessions": [_session_payload()]}
        )

    app.dependency_overrides.clear()

    assert readable.status_code == 200
    assert forbidden.status_code == 403


def test_report_surfaces_redline_evidence_before_anything_else(db, tenant):
    """踩线会话的报告必须先说清"哪句话踩了哪条线"。

    回归点：早先报告只给 total=0 与 grade，`redline=True` 却指不出原句，
    `suggestions` 还在讲"要有推进动作"——最该说的事反而没说。
    """
    _import(
        db,
        tenant.workspace_id,
        _session_payload(
            "s-red",
            "小李",
            messages=[
                ("user", "宝宝一岁能吃吗", 0),
                ("bot", "百分百保证安全，绝对没问题", 10),
            ],
        ),
    )
    session_id = db.scalar(select(QaSession.id).where(QaSession.external_id == "s-red"))

    report = service.get_report(db, tenant.workspace_id, session_id)

    assert report is not None
    assert report.score.redline is True
    assert report.redline_evidence
    assert report.redline_evidence[0].key == "absolute_promise"
    assert report.redline_evidence[0].phrase == "百分百"
    assert report.redline_evidence[0].seq == 1
    assert "红线" in report.suggestions[0]


def test_export_carries_redline_evidence_for_training_data(db, tenant):
    _import(
        db,
        tenant.workspace_id,
        _session_payload(
            "s-red",
            "小李",
            messages=[
                ("user", "能便宜点吗", 0),
                ("bot", "加微信私下转账给你便宜", 10),
            ],
        ),
    )

    export = service.export_low_scores(db, tenant.workspace_id, threshold=0.0)

    assert len(export.items) == 1
    item = export.items[0]
    assert item.redline is True
    assert item.redline_rules == ["private_deal"]
    assert item.redline_evidence[0].phrase == "加微信"
