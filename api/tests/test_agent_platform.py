from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker

from app.agents.platform_graph import PlatformWorkflowDependencies, build_platform_workflow_graph
from app.agents.evaluation import load_platform_eval_cases
from app.agents.policy import AgentPolicyError, authorize_tool
from app.agents.registry import (
    AGENT_REGISTRY,
    COORDINATOR_AGENT,
    INVENTORY_OPERATIONS_AGENT,
    WORKFLOW_REGISTRY,
)
from app.agents import tool_gateway
from app.agents.tool_gateway import ToolExecutionContext, ToolGateway
from app.api.routes import agent_platform
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    AgentRun,
    AgentApproval,
    AgentApprovalStatus,
    AgentMemory,
    AgentMemoryStatus,
    AgentRiskLevel,
    AgentTask,
    AgentTaskStatus,
    AgentRunStatus,
    Base,
    OutboxEvent,
    Platform,
    Product,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
    AgentRunOperation,
    CreativePlan,
)
from app.services import agent_maintenance
from app.services import agent_workflows
from app.services.agent_tasks import (
    AgentTaskStateError,
    cancel_agent_task,
    decide_agent_approval,
)

from support import make_engine, make_session_override


def test_catalog_exposes_one_coordinator_eight_specialists_and_seven_workflows() -> None:
    """验证“catalogexposesonecoordinatoreightspecialistsandsevenworkflows”场景，确保重构后仍满足预期业务与安全边界。"""
    assert COORDINATOR_AGENT in AGENT_REGISTRY
    assert len(AGENT_REGISTRY) == 9
    assert len(WORKFLOW_REGISTRY) == 7


def test_each_platform_workflow_has_six_fixed_eval_cases() -> None:
    """验证“each平台工作流hassix固定评测用例”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    cases = load_platform_eval_cases()
    assert len(cases) == 42
    for workflow_key in WORKFLOW_REGISTRY:
        workflow_cases = [case for case in cases if case.workflow_key == workflow_key]
        assert {case.category for case in workflow_cases} == {
            "routing",
            "context",
            "policy",
            "quality",
            "resilience",
            "approval",
        }


def test_policy_blocks_tool_outside_allowlist_and_requires_r3_owner() -> None:
    """验证“policyblocks工具outside白名单andrequiresr3owner”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    with pytest.raises(AgentPolicyError):
        authorize_tool(
            agent_key=INVENTORY_OPERATIONS_AGENT,
            tool_key="content.image.generate",
            workspace_role=UserRole.OWNER,
        )

    operator_decision = authorize_tool(
        agent_key=INVENTORY_OPERATIONS_AGENT,
        tool_key="inventory.adjust",
        workspace_role=UserRole.OPERATOR,
    )
    owner_decision = authorize_tool(
        agent_key=INVENTORY_OPERATIONS_AGENT,
        tool_key="inventory.adjust",
        workspace_role=UserRole.OWNER,
    )

    assert operator_decision.allowed is False
    assert owner_decision.allowed is True
    assert owner_decision.requires_approval is True


def test_workspace_task_api_writes_task_run_and_outbox_atomically(monkeypatch) -> None:
    """验证“Workspace任务apiwrites任务执行andOutboxatomically”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    monkeypatch.setattr(agent_platform, "kick_outbox_dispatcher", lambda: None)

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/workspaces/{workspace_id}/agent/tasks",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_id)},
            json={
                "workflow_key": "inventory_operations",
                "context_scope": {"product_id": product_id},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["workflow_key"] == "inventory_operations"
    with Session(engine) as db:
        task = db.get(AgentTask, response.json()["id"])
        run = db.query(AgentRun).filter_by(task_id=task.id).one()
        outbox = db.query(OutboxEvent).filter_by(aggregate_id=str(run.id)).one()
        assert task.workspace_id == workspace_id
        assert run.workspace_id == workspace_id
        assert outbox.event_type == "workflow.run.requested"


def test_workspace_task_api_rejects_cross_workspace_context(monkeypatch) -> None:
    """验证“Workspace任务apirejectscrossWorkspace上下文”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, _product_id, other_product_id = _seed(engine)
    monkeypatch.setattr(agent_platform, "kick_outbox_dispatcher", lambda: None)

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/workspaces/{workspace_id}/agent/tasks",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_id)},
            json={
                "workflow_key": "product_diagnosis_optimization",
                "context_scope": {"product_id": other_product_id},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_workspace_can_disable_single_workflow(monkeypatch) -> None:
    """验证“Workspacecandisablesingle工作流”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        workspace = db.get(Workspace, workspace_id)
        workspace.settings = {"agent_workflow_flags": {"inventory_operations": False}}
        db.commit()
    monkeypatch.setattr(agent_platform, "kick_outbox_dispatcher", lambda: None)
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/workspaces/{workspace_id}/agent/tasks",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_id)},
            json={
                "workflow_key": "inventory_operations",
                "context_scope": {"product_id": product_id},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_maintenance_recovers_expired_worker_lease(monkeypatch) -> None:
    """验证“maintenancerecovers过期worker租约”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        task = AgentTask(
            workspace_id=workspace_id,
            requested_by_user_id=user_id,
            workflow_key="inventory_operations",
            workflow_version="1.0.0",
            title="库存运营",
            status=AgentTaskStatus.RUNNING,
            risk_level="R1",
            context_scope={"product_id": product_id},
            input_payload={},
            result_summary={},
            version_manifest={},
        )
        db.add(task)
        db.flush()
        run = AgentRun(
            thread_id="00000000-0000-0000-0000-000000000099",
            workspace_id=workspace_id,
            task_id=task.id,
            product_id=product_id,
            requested_by_user_id=user_id,
            workflow_key="inventory_operations",
            workflow_version="1.0.0",
            status=AgentRunStatus.RUNNING,
            attempt=1,
            revision_count=0,
            max_revisions=1,
            budget={},
            usage={},
            version_manifest={},
            context_snapshot={},
            draft_output={},
            quality_report={},
            final_output={},
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            timeout_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr(agent_maintenance, "SessionLocal", sessionmaker(bind=engine))

    result = agent_maintenance.maintain_agent_runtime()

    assert result["recovered"] == 1
    with Session(engine) as db:
        run = db.get(AgentRun, run_id)
        assert run.status == AgentRunStatus.QUEUED
        assert run.attempt == 2
        assert db.query(OutboxEvent).filter_by(aggregate_id=str(run_id)).count() == 1


def test_tool_gateway_is_workspace_scoped_and_idempotent(monkeypatch) -> None:
    """验证“工具gateway判断Workspacescopedandidempotent”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        run = AgentRun(
            thread_id="00000000-0000-0000-0000-000000000100",
            workspace_id=workspace_id,
            product_id=product_id,
            requested_by_user_id=user_id,
            workflow_key="product_diagnosis_optimization",
            workflow_version="1.0.0",
            status=AgentRunStatus.RUNNING,
            revision_count=0,
            max_revisions=1,
            budget={},
            usage={},
            version_manifest={},
            context_snapshot={},
            draft_output={},
            quality_report={},
            final_output={},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    calls = []
    monkeypatch.setattr(tool_gateway, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(
        tool_gateway,
        "generate_product_diagnosis",
        lambda product: calls.append(product.id) or {"positioning": "测试定位"},
    )
    gateway = ToolGateway(
        ToolExecutionContext(
            workspace_id=workspace_id,
            user_id=user_id,
            workspace_role=UserRole.OPERATOR,
            run_id=run_id,
        )
    )

    first = gateway.execute(
        agent_key="product_strategy_agent",
        tool_key="diagnosis.draft",
        input_payload={"product_id": product_id},
    )
    second = gateway.execute(
        agent_key="product_strategy_agent",
        tool_key="diagnosis.draft",
        input_payload={"product_id": product_id},
    )

    assert first == second == {"positioning": "测试定位"}
    assert calls == [product_id]
    with Session(engine) as db:
        assert db.query(AgentRunOperation).filter_by(run_id=run_id).count() == 1


def test_creative_workflow_persists_editable_business_artifacts_once(monkeypatch) -> None:
    """验证“创意工作流persistseditablebusinessartifactsonce”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        task = AgentTask(
            workspace_id=workspace_id,
            requested_by_user_id=user_id,
            workflow_key="creative_content_production",
            workflow_version="1.0.0",
            title="创意与素材生产",
            status=AgentTaskStatus.RUNNING,
            risk_level="R1",
            context_scope={"product_id": product_id},
            input_payload={},
            result_summary={},
            version_manifest={},
        )
        db.add(task)
        db.flush()
        run = AgentRun(
            thread_id="00000000-0000-0000-0000-000000000101",
            workspace_id=workspace_id,
            task_id=task.id,
            product_id=product_id,
            requested_by_user_id=user_id,
            workflow_key="creative_content_production",
            workflow_version="1.0.0",
            status=AgentRunStatus.RUNNING,
            revision_count=0,
            max_revisions=1,
            budget={},
            usage={},
            version_manifest={},
            context_snapshot={},
            draft_output={},
            quality_report={},
            final_output={},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr(agent_workflows, "SessionLocal", sessionmaker(bind=engine))
    outputs = {
        "creative_strategy_agent": {
            "main_image_plans": [{"title": "场景主图", "angle": "场景"}],
            "video_script_plans": [{"title": "痛点脚本", "angle": "痛点"}],
        }
    }

    first = agent_workflows._finalize_generic_run(run_id, outputs)
    second = agent_workflows._finalize_generic_run(run_id, outputs)

    assert first == second
    assert len(first["artifacts"]["creative_plan_ids"]) == 2
    with Session(engine) as db:
        assert db.query(CreativePlan).filter_by(agent_run_id=run_id).count() == 2


def test_r1_platform_graph_completes_without_approval() -> None:
    """验证“r1平台工作流图completeswithout审批”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    workflow = WORKFLOW_REGISTRY["inventory_operations"]
    finalized: list[dict[str, Any]] = []
    graph = build_platform_workflow_graph(
        workflow=workflow,
        checkpointer=InMemorySaver(),
        deps=_graph_dependencies(finalized),
    )
    config = {"configurable": {"thread_id": "inventory-workflow"}}

    graph.invoke({"run_id": 1, "task_id": 2, "workspace_id": 3}, config=config)
    state = graph.get_state(config)

    assert state.next == ()
    assert state.values["final_status"] == "completed"
    assert finalized


def test_r2_platform_graph_interrupts_then_resumes() -> None:
    """验证“r2平台工作流图interruptsthenresumes”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    workflow = WORKFLOW_REGISTRY["growth_ad_experiment_loop"]
    finalized: list[dict[str, Any]] = []
    graph = build_platform_workflow_graph(
        workflow=workflow,
        checkpointer=InMemorySaver(),
        deps=_graph_dependencies(finalized),
    )
    config = {"configurable": {"thread_id": "growth-workflow"}}

    graph.invoke({"run_id": 1, "task_id": 2, "workspace_id": 3}, config=config)
    assert graph.get_state(config).next == ("approval_gate",)

    graph.invoke(Command(resume={"decision": "approve"}), config=config)
    state = graph.get_state(config)
    assert state.next == ()
    assert state.values["final_status"] == "completed"
    assert finalized


def test_quality_gate_blocks_finalize_when_report_fails() -> None:
    """验证“质量gateblocks完成落库whenreportfails”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    workflow = WORKFLOW_REGISTRY["inventory_operations"]
    finalized: list[dict[str, Any]] = []
    dependencies = _graph_dependencies(finalized)
    dependencies = PlatformWorkflowDependencies(
        load_context=dependencies.load_context,
        run_specialist=dependencies.run_specialist,
        evaluate_quality=lambda _context, _outputs: {"passed": False, "score": 0},
        finalize=dependencies.finalize,
        record_event=dependencies.record_event,
    )
    graph = build_platform_workflow_graph(
        workflow=workflow,
        checkpointer=InMemorySaver(),
        deps=dependencies,
    )
    config = {"configurable": {"thread_id": "quality-failure"}}

    graph.invoke({"run_id": 1, "task_id": 2, "workspace_id": 3}, config=config)
    state = graph.get_state(config)

    assert state.values["final_status"] == "quality_failed"
    assert finalized == []


def test_duplicate_delivery_cannot_claim_running_run(monkeypatch) -> None:
    """验证“duplicatedeliverycannot认领running执行”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        task = _task(workspace_id, user_id, product_id, status=AgentTaskStatus.QUEUED)
        db.add(task)
        db.flush()
        run = _run(task, product_id, user_id, status=AgentRunStatus.QUEUED)
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr(agent_workflows, "SessionLocal", sessionmaker(bind=engine))

    first = agent_workflows._claim_run(run_id, is_resume=False, lease_owner="worker-a")
    second = agent_workflows._claim_run(run_id, is_resume=False, lease_owner="worker-b")

    assert first is not None
    assert second is None


def test_cancelled_task_closes_pending_approval_and_cannot_resume() -> None:
    """验证“cancelled任务closespending审批andcannotresume”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        task = _task(
            workspace_id,
            user_id,
            product_id,
            status=AgentTaskStatus.AWAITING_APPROVAL,
            risk_level=AgentRiskLevel.R2,
        )
        db.add(task)
        db.flush()
        run = _run(task, product_id, user_id, status=AgentRunStatus.AWAITING_REVIEW)
        db.add(run)
        db.flush()
        approval = AgentApproval(
            workspace_id=workspace_id,
            task_id=task.id,
            run_id=run.id,
            requested_by_user_id=user_id,
            action_key="workflow.accept_result",
            risk_level=AgentRiskLevel.R2,
            status=AgentApprovalStatus.PENDING,
            request_payload={},
            decision_payload={},
        )
        db.add(approval)
        db.flush()

        cancel_agent_task(db, task=task)

        assert approval.status == AgentApprovalStatus.CANCELLED
        with pytest.raises(AgentTaskStateError):
            decide_agent_approval(
                db,
                approval=approval,
                reviewer=db.get(User, user_id),
                reviewer_role=UserRole.OPERATOR,
                decision="approve",
                note=None,
                decision_payload={},
            )


def test_expired_approval_cannot_resume_workflow() -> None:
    """验证“过期审批cannotresume工作流”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        task = _task(
            workspace_id,
            user_id,
            product_id,
            status=AgentTaskStatus.AWAITING_APPROVAL,
            risk_level=AgentRiskLevel.R2,
        )
        db.add(task)
        db.flush()
        run = _run(task, product_id, user_id, status=AgentRunStatus.AWAITING_REVIEW)
        db.add(run)
        db.flush()
        approval = AgentApproval(
            workspace_id=workspace_id,
            task_id=task.id,
            run_id=run.id,
            requested_by_user_id=user_id,
            action_key="workflow.accept_result",
            risk_level=AgentRiskLevel.R2,
            status=AgentApprovalStatus.PENDING,
            request_payload={},
            decision_payload={},
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.add(approval)
        db.commit()
        approval_id = approval.id

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/workspaces/{workspace_id}/agent/approvals/{approval_id}/decision",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_id)},
            json={"decision": "approve", "decision_payload": {}},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    with Session(engine) as db:
        approval = db.get(AgentApproval, approval_id)
        run = db.get(AgentRun, approval.run_id)
        task = db.get(AgentTask, approval.task_id)
        assert approval.status == AgentApprovalStatus.EXPIRED
        assert run.status == AgentRunStatus.CANCELLED
        assert task.status == AgentTaskStatus.CANCELLED


def test_memory_context_honors_scope_and_effective_window() -> None:
    """验证“记忆上下文honors作用域and生效window”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, other_product_id = _seed(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        task = _task(workspace_id, user_id, product_id, status=AgentTaskStatus.RUNNING)
        db.add(task)
        db.flush()
        rows = [
            AgentMemory(workspace_id=workspace_id, created_by_user_id=user_id, scope_type="workspace", scope_id=None, memory_key="workspace", content={}, source_type="user_confirmed", source_reference={}, status=AgentMemoryStatus.ACTIVE, effective_at=now),
            AgentMemory(workspace_id=workspace_id, created_by_user_id=user_id, scope_type="product", scope_id=str(product_id), memory_key="current-product", content={}, source_type="user_confirmed", source_reference={}, status=AgentMemoryStatus.ACTIVE, effective_at=now),
            AgentMemory(workspace_id=workspace_id, created_by_user_id=user_id, scope_type="product", scope_id=str(other_product_id), memory_key="other-product", content={}, source_type="user_confirmed", source_reference={}, status=AgentMemoryStatus.ACTIVE, effective_at=now),
            AgentMemory(workspace_id=workspace_id, created_by_user_id=user_id, scope_type="user", scope_id=str(user_id), memory_key="current-user", content={}, source_type="user_confirmed", source_reference={}, status=AgentMemoryStatus.ACTIVE, effective_at=now),
            AgentMemory(workspace_id=workspace_id, created_by_user_id=user_id, scope_type="workspace", scope_id=None, memory_key="expired", content={}, source_type="user_confirmed", source_reference={}, status=AgentMemoryStatus.ACTIVE, effective_at=now - timedelta(days=2), expires_at=now - timedelta(days=1)),
        ]
        db.add_all(rows)
        db.flush()

        memories = agent_workflows._memory_context(db, task)

    assert {memory["memory_key"] for memory in memories} == {
        "workspace",
        "current-product",
        "current-user",
    }


def test_existing_workspace_rejects_user_without_membership() -> None:
    """验证“existingWorkspacerejects用户withoutmembership”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        workspace = Workspace(slug="private", name="私有团队")
        user = User(email="nomember@example.com", display_name="No Member", role=UserRole.OPERATOR)
        db.add_all([workspace, user])
        db.commit()
        user_id = user.id
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).get(
            "/api/v1/products",
            headers={"Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_workspace_viewer_cannot_use_legacy_edit_endpoint() -> None:
    """验证“Workspaceviewercannotuselegacyedit端点”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = make_engine()
    Base.metadata.create_all(engine)
    workspace_id, user_id, product_id, _other_product_id = _seed(engine)
    with Session(engine) as db:
        membership = db.query(WorkspaceMembership).filter_by(
            workspace_id=workspace_id,
            user_id=user_id,
        ).one()
        membership.role = UserRole.VIEWER
        db.commit()
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).patch(
            f"/api/v1/products/{product_id}",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_id)},
            json={"name": "越权修改"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def _graph_dependencies(finalized: list[dict[str, Any]]) -> PlatformWorkflowDependencies:
    """构造或执行测试所需的工作流图dependencies，让测试数据、依赖替身与断言保持集中。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    def finalize(_run_id: int, outputs: dict[str, Any]) -> dict[str, Any]:
        """构造或执行测试所需的完成落库，让测试数据、依赖替身与断言保持集中。"""
        finalized.append(outputs)
        return outputs

    return PlatformWorkflowDependencies(
        load_context=lambda _task_id: {"product": {"id": 1, "name": "测试商品"}},
        run_specialist=lambda agent, _context, _outputs: {"agent": agent, "result": "ok"},
        evaluate_quality=lambda _context, _outputs: {"passed": True, "score": 100},
        finalize=finalize,
        record_event=lambda *_args: None,
    )


def _task(
    workspace_id: int,
    user_id: int,
    product_id: int,
    *,
    status: AgentTaskStatus,
    risk_level: AgentRiskLevel = AgentRiskLevel.R1,
) -> AgentTask:
    """构造或执行测试所需的任务，让测试数据、依赖替身与断言保持集中。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    return AgentTask(
        workspace_id=workspace_id,
        requested_by_user_id=user_id,
        workflow_key="inventory_operations",
        workflow_version="1.0.0",
        title="库存运营",
        status=status,
        risk_level=risk_level,
        context_scope={"product_id": product_id},
        input_payload={},
        result_summary={},
        version_manifest={},
    )


def _run(
    task: AgentTask,
    product_id: int,
    user_id: int,
    *,
    status: AgentRunStatus,
) -> AgentRun:
    """构造或执行测试所需的执行，让测试数据、依赖替身与断言保持集中。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    return AgentRun(
        thread_id=f"00000000-0000-0000-0000-{task.id:012d}",
        workspace_id=task.workspace_id,
        task_id=task.id,
        product_id=product_id,
        requested_by_user_id=user_id,
        workflow_key=task.workflow_key,
        workflow_version=task.workflow_version,
        status=status,
        attempt=1,
        revision_count=0,
        max_revisions=1,
        budget={},
        usage={},
        version_manifest={},
        context_snapshot={},
        draft_output={},
        quality_report={},
        final_output={},
    )


def _seed(engine) -> tuple[int, int, int, int]:
    """构造或执行测试所需的测试数据，让测试数据、依赖替身与断言保持集中。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    with Session(engine) as db:
        workspace = Workspace(slug="team-a", name="团队 A")
        other_workspace = Workspace(slug="team-b", name="团队 B")
        user = User(
            email="operator@example.com",
            display_name="Operator",
            role=UserRole.OPERATOR,
        )
        db.add_all([workspace, other_workspace, user])
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=UserRole.OPERATOR,
            )
        )
        product = Product(
            workspace_id=workspace.id,
            name="团队 A 商品",
            platform=Platform.DOUYIN,
        )
        other_product = Product(
            workspace_id=other_workspace.id,
            name="团队 B 商品",
            platform=Platform.TMALL,
        )
        db.add_all([product, other_product])
        db.commit()
        return workspace.id, user.id, product.id, other_product.id


def _user(user_id: int, role: UserRole) -> User:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    return User(
        id=user_id,
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
    )


