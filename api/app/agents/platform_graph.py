from dataclasses import dataclass
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.registry import QUALITY_COMPLIANCE_AGENT, WorkflowDefinition


class PlatformWorkflowState(TypedDict, total=False):
    run_id: int
    task_id: int
    workspace_id: int
    context_scope: dict[str, Any]
    context: dict[str, Any]
    agent_outputs: dict[str, Any]
    quality_report: dict[str, Any]
    approval: dict[str, Any]
    final_output: dict[str, Any]
    final_status: str


@dataclass(frozen=True)
class PlatformWorkflowDependencies:
    load_context: Callable[[int], dict[str, Any]]
    run_specialist: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
    evaluate_quality: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    finalize: Callable[[int, dict[str, Any]], dict[str, Any]]
    record_event: Callable[[int, str, str | None, dict[str, Any]], None]


def build_platform_workflow_graph(
    *,
    workflow: WorkflowDefinition,
    checkpointer: Any,
    deps: PlatformWorkflowDependencies,
):
    """构建平台工作流工作流图，注册节点、条件路由、人审中断和 checkpoint，返回可执行编排图。"""
    # 节点只通过结构化状态和受控工具交换数据，禁止绕过策略边界写入结果。
    def load_context_node(state: PlatformWorkflowState) -> dict[str, Any]:
        """执行加载上下文节点，从共享状态读取必要输入，并只返回当前节点负责的增量字段。"""
        context = deps.load_context(state["task_id"])
        deps.record_event(state["run_id"], "context_loaded", None, {"context": context})
        return {"context": context, "agent_outputs": {}}

    def specialist_node(agent_key: str):
        """执行领域 Agent节点，从共享状态读取必要输入，并只返回当前节点负责的增量字段。"""
        # 节点只通过结构化状态和受控工具交换数据，禁止绕过策略边界写入结果。
        def execute(state: PlatformWorkflowState) -> dict[str, Any]:
            """执行领域 Agent节点的受观测业务步骤，并把结构化增量交回图节点。"""
            # 每个领域 Agent 只追加自己的输出键，保留前序结果供后续节点与质量门读取。
            outputs = dict(state.get("agent_outputs", {}))
            output = deps.run_specialist(agent_key, state["context"], outputs)
            outputs[agent_key] = output
            deps.record_event(
                state["run_id"],
                "agent_step_completed",
                agent_key,
                {"output": output},
            )
            return {"agent_outputs": outputs}

        return execute

    def quality_node(state: PlatformWorkflowState) -> dict[str, Any]:
        """执行质量节点，从共享状态读取必要输入，并只返回当前节点负责的增量字段。"""
        report = deps.evaluate_quality(state["context"], state.get("agent_outputs", {}))
        deps.record_event(
            state["run_id"],
            "quality_review_completed",
            QUALITY_COMPLIANCE_AGENT,
            {"quality_report": report},
        )
        return {"quality_report": report}

    def approval_node(state: PlatformWorkflowState) -> dict[str, Any]:
        """执行审批节点，从共享状态读取必要输入，并只返回当前节点负责的增量字段。"""
        # interrupt 把草稿和风险快照持久化到 checkpoint，审批后由同一 thread_id 恢复。
        decision = interrupt(
            {
                "action_key": "workflow.accept_result",
                "risk_level": workflow.default_risk_level.value,
                "workflow_key": workflow.key,
                "draft_output": state.get("agent_outputs", {}),
                "quality_report": state.get("quality_report", {}),
            }
        )
        return {"approval": decision}

    def finalize_node(state: PlatformWorkflowState) -> dict[str, Any]:
        """执行完成落库节点，从共享状态读取必要输入，并只返回当前节点负责的增量字段。"""
        # 节点只通过结构化状态和受控工具交换数据，禁止绕过策略边界写入结果。
        outputs = state.get("agent_outputs", {})
        edited_output = state.get("approval", {}).get("edited_output")
        if isinstance(edited_output, dict):
            outputs = edited_output
            report = deps.evaluate_quality(state["context"], outputs)
            deps.record_event(
                state["run_id"],
                "edited_output_quality_review_completed",
                QUALITY_COMPLIANCE_AGENT,
                {"quality_report": report},
            )
            if report.get("passed") is not True:
                return {
                    "agent_outputs": outputs,
                    "quality_report": report,
                    "final_status": "quality_failed",
                }
        final_output = deps.finalize(state["run_id"], outputs)
        return {
            "agent_outputs": outputs,
            "final_output": final_output,
            "final_status": "completed",
        }

    def quality_failed_node(_state: PlatformWorkflowState) -> dict[str, Any]:
        """执行质量失败节点，从共享状态读取必要输入，并只返回当前节点负责的增量字段。"""
        return {"final_status": "quality_failed"}

    def route_after_quality(state: PlatformWorkflowState) -> str:
        """路由after质量，依据结构化状态选择下一个已注册节点，避免隐式或无界跳转。"""
        return "passed" if state.get("quality_report", {}).get("passed") is True else "failed"

    graph = StateGraph(PlatformWorkflowState)
    graph.add_node("load_context", load_context_node)
    graph.add_node("quality_compliance", quality_node)
    graph.add_node("approval_gate", approval_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("quality_failed", quality_failed_node)
    graph.add_edge(START, "load_context")

    previous_node = "load_context"
    for agent_key in workflow.agents:
        if agent_key == QUALITY_COMPLIANCE_AGENT:
            continue
        graph.add_node(agent_key, specialist_node(agent_key))
        graph.add_edge(previous_node, agent_key)
        previous_node = agent_key
    graph.add_edge(previous_node, "quality_compliance")
    if workflow.default_risk_level.value in {"R2", "R3"}:
        graph.add_conditional_edges(
            "quality_compliance",
            route_after_quality,
            {"passed": "approval_gate", "failed": "quality_failed"},
        )
        graph.add_edge("approval_gate", "finalize")
    else:
        graph.add_conditional_edges(
            "quality_compliance",
            route_after_quality,
            {"passed": "finalize", "failed": "quality_failed"},
        )
    graph.add_edge("finalize", END)
    graph.add_edge("quality_failed", END)
    return graph.compile(checkpointer=checkpointer)
