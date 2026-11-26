"""直播切片（P08）测试。

分三层：
- 转写解析与切片算法是纯函数，钉住"什么句子进得来""切出来的边界对不对"；
- 服务层验证落库、重挑保留人工结论；
- 接口层验证租户隔离与权限。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.live import rules, service
from app.live.schemas import SessionCreate, TranscriptItem
from app.main import app
from app.models import Base, Contract  # noqa: F401  确保所有模型都被注册
from tests.dataplatform_support import auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/live"

# 一段有卖点、有寒暄、有长段独白的直播转写。切片的难点全在这里：
# 寒暄不该被选中，长段独白不该被硬切。
TRANSCRIPT = [
    {"start": 0, "end": 8, "text": "大家好，欢迎来到直播间，稍等我看一下网络。"},
    {"start": 8, "end": 16, "text": "听得到吗？扣个 1 我看看。"},
    {"start": 16, "end": 26, "text": "今天这款低糖燕麦饼干，到手价只要 29.9 元，原价 49.9。"},
    {"start": 26, "end": 34, "text": "限量 500 份，只剩最后 80 单了，抢完就没有了。"},
    {"start": 34, "end": 42, "text": "配料表很干净，无添加蔗糖，独立包装好带。"},
    {"start": 42, "end": 50, "text": "买一送一，还加赠一个收纳袋，包邮到家。"},
    {"start": 50, "end": 58, "text": "喜欢的直接点击下方购物车下单，手慢无。"},
    {"start": 58, "end": 70, "text": "好那我们接着说下一个品，这个稍后再讲。"},
]


def _segments(raw=None):
    return rules.parse_transcript(raw if raw is not None else TRANSCRIPT)


# --- 转写解析 -----------------------------------------------------------------


def test_parse_keeps_order_and_fills_missing_end():
    parsed = rules.parse_transcript(
        [
            {"start": 30, "text": "第二句"},
            {"start": 10, "text": "第一句"},
        ]
    )

    assert [item.start for item in parsed] == [10, 30]
    assert parsed[0].end == 30  # 用下一段的 start 补齐
    assert parsed[1].end > 30  # 最后一段按字数估


def test_parse_drops_segments_without_start():
    """没有时间码的句子对不上视频，留着只会让切片边界错位。"""
    parsed = rules.parse_transcript(
        [{"start": 0, "text": "有效"}, {"text": "没有起点"}, {"start": "abc", "text": "起点不是数字"}]
    )

    assert len(parsed) == 1


def test_parse_accepts_alternate_field_names():
    parsed = rules.parse_transcript([{"start_time": 5, "end_time": 9, "text": "你好"}])

    assert parsed[0].start == 5
    assert parsed[0].end == 9


# --- 切片 ---------------------------------------------------------------------


def test_cut_finds_the_selling_window():
    clips = rules.cut(_segments())

    assert clips
    # 价格锚点必须被某条候选抓到——它是这场直播最硬的卖点
    assert any("29.9" in item.transcript for item in clips)
    # 榜首那条一定同时有卖点和时长优势，且不落在开头的寒暄里
    best = clips[0]
    assert best.start >= 8
    assert best.end <= 58


def test_trailing_farewell_never_lands_at_the_end_of_a_clip():
    """往右扩到够长就停，不判断那句是不是在道别——实测「我们下次见」进了候选结尾。"""
    raw = [
        {"start": 0, "end": 5, "text": "到手价只要 79。"},
        {"start": 5, "end": 10, "text": "限量 100 件。"},
        {"start": 10, "end": 15, "text": "抢完就下架。"},
        {"start": 15, "end": 20, "text": "我们下次见。"},
    ]

    clips = rules.cut(_segments(raw))

    assert clips
    assert all("我们下次见" not in item.transcript for item in clips)
    # 削掉道别后不足 12 秒，会往左补一句回来，所以时长仍然合规
    assert all(item.end - item.start >= rules.MIN_SECONDS for item in clips)


def test_farewell_is_kept_when_cutting_it_would_leave_a_fragment():
    """补不出来时宁可多带一句道别，也别交出 8 秒碎片——碎片讲不完一个卖点。"""
    raw = [
        {"start": 0, "end": 6, "text": "到手价只要 79。"},
        {"start": 6, "end": 12, "text": "我们下次见。"},
    ]

    clips = rules.cut(_segments(raw))

    assert clips
    assert "我们下次见" in clips[0].transcript
    assert not any("已剔掉" in reason for reason in clips[0].reasons)


def test_closing_sentence_that_carries_a_selling_point_is_not_trimmed():
    """边界那句本身可能是锚点，削掉就把卖点削没了。"""
    raw = [
        {"start": 0, "end": 5, "text": "到手价只要 79。"},
        {"start": 5, "end": 10, "text": "限量 100 件。"},
        {"start": 10, "end": 16, "text": "谢谢大家，我们下次见，到手价 79。"},
    ]

    clips = rules.cut(_segments(raw))

    assert any("谢谢大家" in item.transcript for item in clips)


def test_cut_does_not_pick_pure_small_talk():
    clips = rules.cut(_segments())
    tops = [item for item in clips if item.score >= 60]

    assert tops
    for item in tops:
        assert "大家好" not in item.transcript[:6]


def test_cut_respects_duration_bounds():
    for item in rules.cut(_segments()):
        span = item.end - item.start
        assert rules.MIN_SECONDS <= span <= rules.MAX_SECONDS


def test_cut_returns_nothing_for_empty_transcript():
    assert rules.cut([]) == []


def test_cut_dedupes_overlapping_candidates():
    clips = rules.cut(_segments())
    for index, first in enumerate(clips):
        for second in clips[index + 1 :]:
            inner = min(first.end, second.end) - max(first.start, second.start)
            if inner <= 0:
                continue
            shorter = min(first.end - first.start, second.end - second.start)
            assert inner / shorter <= rules.MAX_OVERLAP


def test_cut_caps_the_number_of_candidates():
    long_transcript = [
        {"start": index * 10, "end": index * 10 + 9, "text": f"第 {index} 段，只要 9.9 元，限量抢购"}
        for index in range(40)
    ]
    clips = rules.cut(rules.parse_transcript(long_transcript))

    assert len(clips) <= rules.MAX_CLIPS


def test_every_clip_says_why_it_was_picked():
    """没有理由的候选在剪辑台上等于噪声。"""
    for item in rules.cut(_segments()):
        assert item.reasons
        assert item.title


def test_candidate_carries_hook_and_transcript():
    best = rules.cut(_segments())[0]

    assert best.hook
    assert len(best.transcript) > 10


# --- 服务与接口 ---------------------------------------------------------------


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


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _payload() -> SessionCreate:
    return SessionCreate(
        title="10 月 28 日低糖饼干专场",
        product_name="低糖燕麦饼干",
        transcript=[TranscriptItem(**item) for item in TRANSCRIPT],
    )


def test_create_session_persists_clips(db, tenant):
    result = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )

    assert result.status == "cut"
    assert result.clip_count == len(result.clips) > 0
    assert result.duration_seconds == 70


def test_create_session_rejects_empty_transcript(db, tenant):
    with pytest.raises(service.LiveError) as exc:
        service.create_session(
            db,
            tenant.workspace_id,
            user_id=tenant.user.id,
            payload=SessionCreate(title="空场", transcript=[]),
        )

    assert exc.value.status_code == 422


def test_decide_marks_session_reviewed(db, tenant):
    session = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )
    clip_id = session.clips[0].id

    decided = service.decide(db, tenant.workspace_id, clip_id, status="approved")

    assert decided.status == "approved"
    assert decided.decided_at is not None
    assert service.get_session(db, tenant.workspace_id, session.id).status == "reviewed"


def test_decide_rejects_unknown_status(db, tenant):
    session = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )

    with pytest.raises(service.LiveError) as exc:
        service.decide(db, tenant.workspace_id, session.clips[0].id, status="maybe")

    assert exc.value.status_code == 400


def test_rejecting_a_clip_also_stamps_the_time(db, tenant):
    """驳回也是定稿。没有时间戳就分不清"被人否了"和"还没人看"。"""
    session = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )

    decided = service.decide(db, tenant.workspace_id, session.clips[0].id, status="rejected")

    assert decided.status == "rejected"
    assert decided.decided_at is not None
    # 和 created_at 同源（都是数据库时钟），不能差出时区偏移
    assert (decided.decided_at - session.created_at).total_seconds() < 300


def test_recut_keeps_manual_decisions(db, tenant):
    """重挑不能把人工已经看过的结论丢掉——人的判断比机器贵。"""
    session = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )
    approved = session.clips[0]
    service.decide(db, tenant.workspace_id, approved.id, status="approved")

    again = service.recut(db, tenant.workspace_id, session.id)

    kept = [item for item in again.clips if item.id == approved.id]
    assert len(kept) == 1
    assert kept[0].status == "approved"


def test_session_from_other_workspace_is_404(db, tenant):
    other = seed_tenant(db, slug="other", with_user=True)
    session = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )

    with pytest.raises(service.LiveError) as exc:
        service.get_session(db, other.workspace_id, session.id)

    assert exc.value.status_code == 404


def test_summary_counts_by_status(engine, db, tenant):
    session = service.create_session(
        db, tenant.workspace_id, user_id=tenant.user.id, payload=_payload()
    )
    service.decide(db, tenant.workspace_id, session.clips[0].id, status="approved")
    headers, stub = auth_payload(tenant)

    with client(engine, stub) as http:
        response = http.get(f"{BASE}/summary", headers=headers)

    app.dependency_overrides.clear()

    body = response.json()
    assert body["sessions"] == 1
    assert body["approved"] == 1
    assert body["clips"] == session.clip_count


def test_create_endpoint_returns_201(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.post(f"{BASE}/sessions", headers=headers, json=_payload().model_dump())

    app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["clip_count"] > 0
    assert body["clips"][0]["reasons"]


def test_viewer_can_read_but_cannot_cut(engine, db, tenant):
    from types import SimpleNamespace

    from app.models import User, UserRole, WorkspaceMembership

    viewer = User(
        email="live-viewer@example.com", display_name="v", role=UserRole.VIEWER, password_hash="x"
    )
    db.add(viewer)
    db.flush()
    db.add(
        WorkspaceMembership(workspace_id=tenant.workspace_id, user_id=viewer.id, role=UserRole.VIEWER)
    )
    db.commit()
    headers, _ = auth_payload(tenant)

    with client(engine, SimpleNamespace(id=viewer.id, role=UserRole.VIEWER)) as http:
        readable = http.get(f"{BASE}/summary", headers=headers)
        forbidden = http.post(f"{BASE}/sessions", headers=headers, json=_payload().model_dump())

    app.dependency_overrides.clear()

    assert readable.status_code == 200
    assert forbidden.status_code == 403
