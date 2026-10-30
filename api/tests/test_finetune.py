"""模型微调平台（P10）测试。

重点不在"能不能建表"，而在三条边界：
- 样本必须可追溯（source_ref 指回原始记录）；
- 去重必须在入库前完成（同一句模板话术不重复进训练集）；
- 待修样本不进训练集（把待修样本拿去训，等于教模型学错误的回答）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.finetune import builders, service
from app.finetune.schemas import BuildRequest, EvalCase, EvalRequest, JobCreate, JobUpdate
from app.main import app
from app.models import (
    AgentConversation,
    AgentConversationMessage,
    Base,
    QaMessage,
    QaRole,
    QaScore,
    QaSession,
)
from tests.dataplatform_support import auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/finetune"

GOOD_SESSION = [
    ("customer", "这个低糖饼干还有货吗，多少钱"),
    ("agent", "您好，有的，29.9 元一盒，可以帮您留货，稍后发链接给您"),
]
POOR_SESSION = [
    ("customer", "什么时候发货"),
    ("agent", "不知道，自己看详情页"),
]
BOUNDARY_SESSION = [
    ("customer", "你们公司什么时候上市"),
    ("agent", "这个问题我查不到可靠的依据，先不猜答案。您可以说得更具体，或者我帮您转人工。"),
]


def _qa_session(
    db: Session,
    workspace_id: int,
    *,
    external_id: str,
    utterances: list[tuple[str, str]],
    grade: str = "A",
    redline: bool = False,
) -> QaSession:
    session = QaSession(
        workspace_id=workspace_id,
        external_id=external_id,
        agent_name="莉娜",
        channel="douyin",
        started_at=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
    )
    db.add(session)
    db.flush()
    for index, (role, content) in enumerate(utterances):
        db.add(
            QaMessage(
                workspace_id=workspace_id,
                session_id=session.id,
                seq=index,
                role=QaRole.CUSTOMER if role == "customer" else QaRole.AGENT,
                content=content,
            )
        )
    db.add(
        QaScore(
            workspace_id=workspace_id,
            session_id=session.id,
            total=92 if grade == "A" else 45,
            grade=grade,
            breakdown=[],
            redline=redline,
            redline_rules=["rudeness"] if redline else [],
            rule_version="1.0.1",
        )
    )
    db.flush()
    return session


def _cs_conversation(
    db: Session, workspace_id: int, *, user_id: int, utterances: list[tuple[str, str]], track: str = ""
) -> AgentConversation:
    """`track=""` 表示这条会话没有轨道标记——早期直接写库的数据就是这样。"""
    conversation = AgentConversation(
        workspace_id=workspace_id, created_by_user_id=user_id, title="客服会话"
    )
    db.add(conversation)
    db.flush()
    for index, (role, content) in enumerate(utterances):
        db.add(
            AgentConversationMessage(
                conversation_id=conversation.id,
                workspace_id=workspace_id,
                role="user" if role == "customer" else "assistant",
                content=content,
                structured_content={"track": track} if (role == "agent" and track) else {},
            )
        )
    db.flush()
    return conversation


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


# --- 样本构建 -----------------------------------------------------------------


def test_qa_session_with_good_grade_becomes_positive_sample():
    draft = builders.from_qa_session(
        session_id=1, utterances=GOOD_SESSION, grade="A", redline=False
    )

    assert draft is not None
    assert draft.quality == builders.QUALITY_POSITIVE
    assert draft.source == "qa"
    assert draft.source_ref == "qa_session:1"
    assert draft.messages[0]["role"] == "system"
    assert draft.messages[-1]["role"] == "assistant"


def test_redline_session_becomes_needs_fix_sample():
    """红线样本是最值钱的反例——它直接告诉模型"这样说不行"。"""
    draft = builders.from_qa_session(
        session_id=2, utterances=POOR_SESSION, grade="D", redline=True
    )

    assert draft is not None
    assert draft.quality == builders.QUALITY_NEEDS_FIX
    assert "redline" in draft.tags


def test_session_ending_with_customer_turn_is_dropped():
    """客服说完客户又追问一句，这种样本会让模型学到"答完还要等下一句"。"""
    draft = builders.from_qa_session(
        session_id=3,
        utterances=[("agent", "您好，有的"), ("customer", "那我要两箱")],
        grade="A",
        redline=False,
    )

    assert draft is None


def test_too_short_session_is_dropped():
    draft = builders.from_qa_session(
        session_id=4, utterances=[("customer", "在吗"), ("agent", "在")], grade="A", redline=False
    )

    assert draft is None  # 低于 MIN_CHARS


def test_refuse_track_becomes_boundary_sample():
    draft = builders.from_cs_conversation(
        conversation_id=9, utterances=BOUNDARY_SESSION, track="refuse"
    )

    assert draft is not None
    assert draft.quality == builders.QUALITY_BOUNDARY
    assert draft.tags == ["track:refuse"]


def test_unknown_track_is_dropped():
    assert builders.from_cs_conversation(conversation_id=9, utterances=GOOD_SESSION, track="") is None


def test_fingerprint_is_stable_for_same_messages():
    first = builders.from_qa_session(session_id=1, utterances=GOOD_SESSION, grade="A", redline=False)
    second = builders.from_qa_session(session_id=2, utterances=GOOD_SESSION, grade="A", redline=False)

    assert first is not None and second is not None
    assert first.fingerprint() == second.fingerprint()


# --- 数据集 -------------------------------------------------------------------


def test_build_dataset_dedupes_and_records_stats(db, tenant):
    # 两条内容完全相同的会话 + 一条不同轨道的客服会话
    _qa_session(db, tenant.workspace_id, external_id="a", utterances=GOOD_SESSION)
    _qa_session(db, tenant.workspace_id, external_id="b", utterances=GOOD_SESSION)
    _cs_conversation(
        db, tenant.workspace_id, user_id=tenant.user.id,
        utterances=BOUNDARY_SESSION, track="refuse",
    )
    db.commit()

    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id,
        name="10 月回流", sources=["qa", "cs"], limit=100,
    )

    # 相同内容只留一条
    assert dataset.sample_count == 2
    assert dataset.stats["by_source"] == {"qa": 1, "cs": 1}
    assert dataset.stats["by_quality"][builders.QUALITY_POSITIVE] == 1
    assert dataset.stats["by_quality"][builders.QUALITY_BOUNDARY] == 1
    # 被合并掉的那条要写清来自哪一侧，否则"客服会话怎么少了一条"查不出来
    assert dataset.stats["duplicates"] == 1
    assert dataset.stats["duplicates_by_source"] == {"qa": 1}


def test_dedupe_keeps_the_qa_copy_because_its_label_is_harder(db, tenant):
    """同一条对话在 P09 与 P01 各存一份是常态。留 qa：它带分数、等级和红线标记。"""
    _qa_session(db, tenant.workspace_id, external_id="dup", utterances=GOOD_SESSION)
    _cs_conversation(
        db, tenant.workspace_id, user_id=tenant.user.id,
        utterances=GOOD_SESSION, track="faq",
    )
    db.commit()

    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id,
        name="撞车", sources=["qa", "cs"], limit=100,
    )

    assert dataset.sample_count == 1
    assert dataset.stats["by_source"] == {"qa": 1}
    assert dataset.stats["duplicates_by_source"] == {"cs": 1}


def test_build_dataset_without_any_source_raises_422(db, tenant):
    with pytest.raises(service.FinetuneError) as exc:
        service.build_dataset(
            db, tenant.workspace_id, user_id=tenant.user.id,
            name="空集", sources=["qa"], limit=10,
        )

    assert exc.value.status_code == 422
    assert "P09" in exc.value.message


def test_build_dataset_records_why_samples_were_skipped(db, tenant):
    """静默丢弃是数据平台最坏的失败方式：只看到"这次 1 条"，
    没人知道另外 11 条客服会话为什么没进来。"""
    _qa_session(db, tenant.workspace_id, external_id="ok", utterances=GOOD_SESSION)
    # 没有轨道标记的客服会话 + 没评分的会话，都该被丢掉，但都要计数
    _cs_conversation(
        db, tenant.workspace_id, user_id=tenant.user.id,
        utterances=BOUNDARY_SESSION, track="",
    )
    unscored = QaSession(
        workspace_id=tenant.workspace_id,
        external_id="unscored",
        agent_name="莉娜",
        channel="douyin",
        started_at=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
    )
    db.add(unscored)
    db.commit()

    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id,
        name="含丢弃原因", sources=["qa", "cs"], limit=100,
    )

    assert dataset.sample_count == 1
    assert dataset.stats["skipped"] == {"cs_missing_track": 1, "qa_unscored": 1}


def test_build_rejects_unknown_source(db, tenant):
    with pytest.raises(service.FinetuneError) as exc:
        service.build_dataset(
            db, tenant.workspace_id, user_id=tenant.user.id,
            name="x", sources=["pdf"], limit=10,
        )

    assert exc.value.status_code == 400


def test_dataset_from_other_workspace_is_404(db, tenant):
    other = seed_tenant(db, slug="other", with_user=True)
    _qa_session(db, tenant.workspace_id, external_id="a", utterances=GOOD_SESSION)
    db.commit()
    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )

    with pytest.raises(service.FinetuneError) as exc:
        service.get_dataset(db, other.workspace_id, dataset.id)

    assert exc.value.status_code == 404


# --- 导出 ---------------------------------------------------------------------


def test_export_excludes_needs_fix_samples(db, tenant):
    _qa_session(db, tenant.workspace_id, external_id="good", utterances=GOOD_SESSION, grade="A")
    _qa_session(
        db, tenant.workspace_id, external_id="bad", utterances=POOR_SESSION, grade="D", redline=True
    )
    db.commit()
    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )

    filename, payload = service.export_jsonl(db, tenant.workspace_id, dataset.id)
    lines = [json.loads(line) for line in payload.strip().split("\n")]

    assert filename.endswith(".jsonl")
    assert len(lines) == 1  # 待修样本没进
    assert lines[0]["messages"][-1]["role"] == "assistant"
    assert "messages" in lines[0]


def test_export_marks_dataset_exported(db, tenant):
    _qa_session(db, tenant.workspace_id, external_id="good", utterances=GOOD_SESSION)
    db.commit()
    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )

    service.export_jsonl(db, tenant.workspace_id, dataset.id)

    refreshed = service.get_dataset(db, tenant.workspace_id, dataset.id)
    assert refreshed.status == "exported"
    assert refreshed.exported_at is not None


def test_export_fails_when_everything_needs_fix(db, tenant):
    _qa_session(
        db, tenant.workspace_id, external_id="bad", utterances=POOR_SESSION, grade="D", redline=True
    )
    db.commit()
    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )

    with pytest.raises(service.FinetuneError) as exc:
        service.export_jsonl(db, tenant.workspace_id, dataset.id)

    assert exc.value.status_code == 422


# --- 任务与评测 ---------------------------------------------------------------


def _dataset(db, tenant):
    _qa_session(db, tenant.workspace_id, external_id="a", utterances=GOOD_SESSION)
    db.commit()
    return service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )


def test_register_job_starts_as_registered(db, tenant):
    """平台不执行训练，所以任务只能是"已登记"，不能是"运行中"。"""
    dataset = _dataset(db, tenant)

    job = service.register_job(
        db, tenant.workspace_id,
        payload=JobCreate(dataset_id=dataset.id, name="sft-1", base_model="qwen-plus"),
    )

    assert job.status == "registered"
    assert job.metrics == {}


def test_register_job_rejects_unknown_dataset(db, tenant):
    with pytest.raises(service.FinetuneError) as exc:
        service.register_job(
            db, tenant.workspace_id,
            payload=JobCreate(dataset_id=999, name="x", base_model="qwen-plus"),
        )

    assert exc.value.status_code == 404


def test_update_job_validates_status(db, tenant):
    dataset = _dataset(db, tenant)
    job = service.register_job(
        db, tenant.workspace_id,
        payload=JobCreate(dataset_id=dataset.id, name="sft-1", base_model="qwen-plus"),
    )

    with pytest.raises(service.FinetuneError) as exc:
        service.update_job(db, tenant.workspace_id, job.id, payload=JobUpdate(status="training", metrics={}))

    assert exc.value.status_code == 400


def test_update_job_to_succeeded_sets_finished_at(db, tenant):
    dataset = _dataset(db, tenant)
    job = service.register_job(
        db, tenant.workspace_id,
        payload=JobCreate(dataset_id=dataset.id, name="sft-1", base_model="qwen-plus"),
    )

    updated = service.update_job(
        db, tenant.workspace_id, job.id,
        payload=JobUpdate(status="succeeded", metrics={"loss": 0.83}, note="外部服务跑完"),
    )

    assert updated.status == "succeeded"
    assert updated.metrics == {"loss": 0.83}
    assert updated.finished_at is not None


def test_record_eval_computes_delta_on_the_same_cases(db, tenant):
    dataset = _dataset(db, tenant)
    job = service.register_job(
        db, tenant.workspace_id,
        payload=JobCreate(dataset_id=dataset.id, name="sft-1", base_model="qwen-plus"),
    )

    updated = service.record_eval(
        db, tenant.workspace_id, job.id,
        payload=EvalRequest(
            cases=[
                EvalCase(question="还有货吗", base_score=60, tuned_score=85),
                EvalCase(question="能退吗", base_score=70, tuned_score=65),
            ],
            note="人工打分",
        ),
    )

    summary = updated.eval_summary
    assert summary["cases"] == 2
    assert summary["base_average"] == 65.0
    assert summary["tuned_average"] == 75.0
    assert summary["delta"] == 10.0
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert len(summary["detail"]) == 2


def test_record_eval_requires_cases(db, tenant):
    dataset = _dataset(db, tenant)
    job = service.register_job(
        db, tenant.workspace_id,
        payload=JobCreate(dataset_id=dataset.id, name="sft-1", base_model="qwen-plus"),
    )

    with pytest.raises(service.FinetuneError) as exc:
        service.record_eval(db, tenant.workspace_id, job.id, payload=EvalRequest(cases=[]))

    assert exc.value.status_code == 422


# --- 接口 ---------------------------------------------------------------------


def test_summary_counts_sources_and_qualities(engine, db, tenant):
    _qa_session(db, tenant.workspace_id, external_id="a", utterances=GOOD_SESSION)
    _qa_session(
        db, tenant.workspace_id, external_id="b", utterances=POOR_SESSION, grade="D", redline=True
    )
    db.commit()
    service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )
    headers, stub = auth_payload(tenant)

    with client(engine, stub) as http:
        response = http.get(f"{BASE}/summary", headers=headers)

    app.dependency_overrides.clear()

    body = response.json()
    assert body["datasets"] == 1
    assert body["samples"] == 2
    assert {row["key"]: row["count"] for row in body["by_source"]} == {"qa": 2}


def test_build_endpoint_returns_201(engine, db, tenant):
    _qa_session(db, tenant.workspace_id, external_id="a", utterances=GOOD_SESSION)
    db.commit()
    headers, stub = auth_payload(tenant)

    with client(engine, stub) as http:
        response = http.post(
            f"{BASE}/datasets", headers=headers,
            json=BuildRequest(name="回流集", sources=["qa"], limit=50).model_dump(),
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["sample_count"] == 1


def test_export_endpoint_sets_attachment_header(engine, db, tenant):
    _qa_session(db, tenant.workspace_id, external_id="a", utterances=GOOD_SESSION)
    db.commit()
    dataset = service.build_dataset(
        db, tenant.workspace_id, user_id=tenant.user.id, name="x", sources=["qa"], limit=10
    )
    headers, stub = auth_payload(tenant)

    with client(engine, stub) as http:
        response = http.get(f"{BASE}/datasets/{dataset.id}/export", headers=headers)

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.strip().startswith("{")


def test_viewer_can_read_but_cannot_build(engine, db, tenant):
    from types import SimpleNamespace

    from app.models import User, UserRole, WorkspaceMembership

    viewer = User(
        email="ft-viewer@example.com", display_name="v", role=UserRole.VIEWER, password_hash="x"
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
        forbidden = http.post(
            f"{BASE}/datasets", headers=headers, json={"name": "x", "sources": ["qa"]}
        )

    app.dependency_overrides.clear()

    assert readable.status_code == 200
    assert forbidden.status_code == 403
