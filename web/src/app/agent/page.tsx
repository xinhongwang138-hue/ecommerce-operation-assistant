import {
  Activity,
  ArrowRight,
  Bot,
  Brain,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  DatabaseZap,
  Layers3,
  Play,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";

import { AgentTaskAutoRefresh } from "@/components/agent-task-auto-refresh";
import { AgentTaskList } from "@/components/task-list";
import { DigitalHumanAssistant } from "@/components/digital-human-assistant";
import {
  AgentApiError,
  getAgentApprovals,
  getAgentCatalog,
  getAgentMemories,
  getAgentTasks,
  getCurrentUser,
  getWorkspaces,
  getWorkspaceProducts,
  getWorkspaceStores,
} from "@/lib/api";
import { getActiveWorkspaceId, getAuthToken } from "@/lib/session";
import type { AgentRiskLevel, AgentTaskStatus } from "@/types";

type AgentCenterProps = {
  searchParams?: Promise<{ workspace?: string; result?: string; section?: string }>;
};

const statusLabels: Record<AgentTaskStatus, string> = {
  queued: "排队中",
  running: "执行中",
  awaiting_approval: "待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const statusTones: Record<AgentTaskStatus, string> = {
  queued: "badge-muted",
  running: "badge-running",
  awaiting_approval: "badge-warning",
  completed: "badge-success",
  failed: "badge-danger",
  cancelled: "badge-muted",
};

const riskTones: Record<AgentRiskLevel, string> = {
  R0: "risk-r0",
  R1: "risk-r1",
  R2: "risk-r2",
  R3: "risk-r3",
};

/** 渲染 AgentCenter 组件，集中展示Agent中心所需的数据、状态和操作入口。 */
export default async function AgentCenter({ searchParams }: AgentCenterProps) {
  const token = await getAuthToken();
  if (!token) {
    redirect("/login");
  }
  const query = searchParams ? await searchParams : {};
  const activeWorkspaceId = await getActiveWorkspaceId();
  let workspaces: Awaited<ReturnType<typeof getWorkspaces>>;
  let currentUser: Awaited<ReturnType<typeof getCurrentUser>>;
  try {
    [workspaces, currentUser] = await Promise.all([getWorkspaces(), getCurrentUser(token)]);
  } catch (error) {
    if (error instanceof AgentApiError && error.status === 401) redirect("/login?expired=1");
    return <AgentServiceError />;
  }
  if (!currentUser) redirect("/login?expired=1");
  const workspace = selectWorkspace(
    workspaces,
    query.workspace ?? activeWorkspaceId?.toString(),
  );
  if (!workspace) {
    return <EmptyWorkspace />;
  }
  const workspaceId = workspace.workspace.id;
  let resources;
  try {
    resources = await Promise.all([
      getAgentCatalog(workspaceId),
      getAgentTasks(workspaceId),
      getAgentApprovals(workspaceId),
      getAgentMemories(workspaceId),
      getWorkspaceProducts(workspaceId),
      getWorkspaceStores(workspaceId),
    ]);
  } catch (error) {
    if (error instanceof AgentApiError && error.status === 401) redirect("/login?expired=1");
    return <AgentServiceError />;
  }
  const [catalog, tasks, approvals, memories, products, stores] = resources;
  const canEdit = workspace.membership.role !== "viewer";
  const completedCount = tasks.filter((task) => task.status === "completed").length;
  const activeCount = tasks.filter((task) => ["queued", "running"].includes(task.status)).length;
  const approvalCount = approvals.length;

  return (
    <main className="flex flex-col gap-6">
      <AgentTaskAutoRefresh active={activeCount > 0} />

      <header className="flex flex-col gap-4 border-b border-[var(--border)] pb-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--primary)]">
            <Bot className="h-4 w-4" aria-hidden="true" />
            Ecommerce Operations Agent
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-[var(--foreground)] sm:text-3xl">统一运营 Agent 中心</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted-foreground)]">
            由一个协调 Agent 选择受控工作流，八个专业 Agent 在 Harness 约束下协作；业务结果仍回到可编辑工作台。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {workspaces.length > 1 ? (
            <form action="/agent/workspaces/select" method="post" className="inline-flex items-center gap-2">
              <select
                name="workspace_id"
                defaultValue={workspaceId}
                className="input h-9 rounded-[10px] px-3 text-sm"
                aria-label="切换 Workspace"
              >
                {workspaces.map((item) => (
                  <option key={item.workspace.id} value={item.workspace.id}>
                    {item.workspace.name}
                  </option>
                ))}
              </select>
              <button className="btn-ghost h-9 px-3 text-sm font-medium">切换</button>
            </form>
          ) : null}
          <Link href="/" className="btn-ghost inline-flex h-9 items-center gap-2 px-3 text-sm font-medium">
            <Activity className="h-4 w-4" aria-hidden="true" />
            业务工作台
          </Link>
          <span className="inline-flex h-9 items-center rounded-full bg-[var(--primary-soft)] px-3 text-sm font-medium text-[var(--primary)]">
            {currentUser?.display_name ?? "当前用户"} · {workspace.workspace.name}
          </span>
        </div>
      </header>

      {query.result ? <ResultBanner result={query.result} /> : null}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric icon={Layers3} label="版本化工作流" value={catalog?.workflows.length ?? 0} gradient="bg-gradient-primary" />
        <Metric icon={Activity} label="进行中任务" value={activeCount} gradient="bg-gradient-info" />
        <Metric icon={CheckCircle2} label="已完成任务" value={completedCount} gradient="bg-gradient-success" />
        <Metric icon={ClipboardCheck} label="待处理审批" value={approvalCount} gradient="bg-gradient-warning" />
      </section>

      <nav className="flex flex-wrap gap-2 rounded-[14px] border border-[var(--border)] bg-[var(--card)] p-2 text-sm">
        {[
          ["#agent-entry", "Agent 入口"],
          ["#task-center", "任务中心"],
          ["#approval-inbox", "审批收件箱"],
          ["#memory", "结构化记忆"],
          ["#catalog", "Agent 目录"],
        ].map(([href, label]) => (
          <a key={href} href={href} className="rounded-[10px] px-3 py-2 font-medium text-[var(--muted-foreground)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--primary)]">
            {label}
          </a>
        ))}
      </nav>

      <section id="agent-entry" className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-[var(--foreground)]">告诉协调 Agent 你的目标</h2>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">协调 Agent 只从已注册工作流中路由，不会自由拼装高风险动作。</p>
            </div>
            <ShieldCheck className="h-6 w-6 text-[var(--primary)]" aria-hidden="true" />
          </div>
          <form action="/agent/tasks/create" method="post" className="mt-5 space-y-4">
            <input type="hidden" name="workspace_id" value={workspaceId} />
            <textarea
              name="intent"
              required
              disabled={!canEdit}
              placeholder="例如：检查这款商品近期的库存和经营风险，并给出下一步建议"
              className="input min-h-28 w-full px-3 py-3 text-sm disabled:opacity-60"
            />
            <ContextSelectors products={products} stores={stores} disabled={!canEdit} />
            <button type="submit" disabled={!canEdit} className="btn-primary inline-flex h-10 items-center gap-2 px-4 text-sm font-medium disabled:opacity-60">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
              创建受控任务
            </button>
          </form>
        </div>

        <div className="card bg-[#16142b] p-5 text-white shadow-[var(--shadow-card)]">
          <div className="flex items-center gap-2 text-sm font-medium text-[#a78bfa]">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" /> Harness 边界
          </div>
          <h2 className="mt-3 text-xl font-semibold">不是把多个 Prompt 串在一起</h2>
          <div className="mt-5 grid gap-3 text-sm text-[#c7c3e0] sm:grid-cols-2">
            {[
              ["身份与租户", "Workspace 强隔离"],
              ["工具策略", "Agent allowlist + 风险分级"],
              ["可靠执行", "Celery + Outbox + 幂等"],
              ["状态恢复", "PostgreSQL Checkpoint"],
              ["人工协作", "R2/R3 数据库审批"],
              ["质量闭环", "事件、评测和版本追踪"],
            ].map(([title, detail]) => (
              <div key={title} className="rounded-[10px] border border-white/10 bg-white/5 p-3">
                <p className="font-medium text-white">{title}</p>
                <p className="mt-1 text-xs text-[#a9a4c4]">{detail}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mt-6">
        <DigitalHumanAssistant workspaceId={workspaceId} products={products} stores={stores} />
      </section>

      <section className="card p-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-[var(--foreground)]">任务模板</h2>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">每个模板对应一个可版本化、可评测、可回退的 LangGraph 工作流。</p>
          </div>
          <span className="text-xs text-[var(--muted-foreground)]">{catalog?.workflows.length ?? 0} 个已启用</span>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {catalog?.workflows.map((workflow) => (
            <form key={workflow.key} action="/agent/tasks/create" method="post" className="card card-hover flex flex-col p-4">
              <input type="hidden" name="workspace_id" value={workspaceId} />
              <input type="hidden" name="workflow_key" value={workflow.key} />
              <div className="flex items-start justify-between gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]"><Play className="h-4 w-4" aria-hidden="true" /></div>
                <span className={`risk-chip ${riskTones[workflow.default_risk_level]}`}>{workflow.default_risk_level}</span>
              </div>
              <h3 className="mt-4 font-semibold text-[var(--foreground)]">{workflow.title}</h3>
              <p className="mt-2 min-h-10 text-sm leading-5 text-[var(--muted-foreground)]">{workflow.description}</p>
              <div className="mt-3"><ContextSelectors products={products} stores={stores} compact disabled={!canEdit} /></div>
              <button type="submit" disabled={!canEdit} className="btn-ghost mt-4 inline-flex h-9 items-center justify-center gap-2 px-3 text-sm font-medium disabled:opacity-60">
                启动工作流 <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </form>
          ))}
        </div>
      </section>

            <section id="task-center" className="card p-5">
        <div className="flex items-center justify-between gap-4">
          <div><h2 className="text-lg font-semibold text-[var(--foreground)]">任务中心</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">Task 表达业务目标，Run 表达实际执行和重试。</p></div>
          <Clock3 className="h-5 w-5 text-[var(--muted-foreground)]" aria-hidden="true" />
        </div>
        <AgentTaskList tasks={tasks} workspaceId={workspaceId} canEdit={canEdit} />
      </section>

      <section id="approval-inbox" className="card border-[var(--warning)]/40 bg-[var(--warning)]/5 p-5">
        <div className="flex items-center gap-3"><ClipboardCheck className="h-5 w-5 text-[var(--warning)]" aria-hidden="true" /><div><h2 className="text-lg font-semibold text-[var(--foreground)]">审批收件箱</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">R2/R3 副作用必须由策略层和数据库审批共同放行。</p></div></div>
        <div className="mt-5 space-y-3">
          {approvals.length === 0 ? <EmptyText text="当前没有待审批动作。" /> : approvals.map((approval) => (
            <div key={approval.id} className="card p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-medium text-[var(--foreground)]">任务 #{approval.task_id} · {approval.action_key}</p><p className="mt-1 text-xs text-[var(--muted-foreground)]">Run #{approval.run_id} · {formatDate(approval.created_at)}</p></div><span className={`risk-chip ${riskTones[approval.risk_level]}`}>{approval.risk_level}</span></div>
              <div className="mt-3"><JsonPanel title="审批内容" value={approval.request_payload} /></div>
              {canEdit ? <div className="mt-4 flex flex-col gap-2"><ApprovalForm approvalId={approval.id} workspaceId={workspaceId} decision="approve" draftOutput={approval.request_payload.draft_output} /><ApprovalForm approvalId={approval.id} workspaceId={workspaceId} decision="reject" /></div> : null}
            </div>
          ))}
        </div>
      </section>

      <section id="memory" className="grid gap-6 xl:grid-cols-[360px_1fr]">
        <div className="card p-5">
          <div className="flex items-center gap-2"><Brain className="h-5 w-5 text-[var(--primary)]" aria-hidden="true" /><h2 className="text-lg font-semibold text-[var(--foreground)]">确认一条长期记忆</h2></div>
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">只保存用户明确确认的结构化事实，不自动吸收全部聊天。</p>
          <form action="/agent/memories/create" method="post" className="mt-4 space-y-3">
            <input type="hidden" name="workspace_id" value={workspaceId} />
            <select name="scope_type" className="input h-10 w-full rounded-[10px] px-3 text-sm"><option value="workspace">工作区</option><option value="store">店铺</option><option value="product">商品</option><option value="user">用户偏好</option></select>
            <input name="scope_id" placeholder="范围 ID（工作区可留空）" className="input h-10 w-full rounded-[10px] px-3 text-sm" />
            <input name="memory_key" required placeholder="键，例如 brand_tone" className="input h-10 w-full rounded-[10px] px-3 text-sm" />
            <textarea name="memory_value" required placeholder="已确认的事实或偏好" className="input min-h-24 w-full rounded-[10px] px-3 py-2 text-sm" />
            <button disabled={!canEdit} className="btn-primary inline-flex h-9 items-center gap-2 px-3 text-sm font-medium disabled:opacity-60"><DatabaseZap className="h-4 w-4" aria-hidden="true" />保存记忆</button>
          </form>
        </div>
        <div className="card p-5">
          <h2 className="text-lg font-semibold text-[var(--foreground)]">有效记忆</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {memories.length === 0 ? <EmptyText text="暂无已确认记忆。" /> : memories.map((memory) => <div key={memory.id} className="card p-4"><div className="flex items-center justify-between gap-3"><p className="font-medium text-[var(--foreground)]">{memory.memory_key}</p><span className="text-xs text-[var(--muted-foreground)]">{memory.scope_type}:{memory.scope_id ?? workspaceId}</span></div><pre className="mt-3 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--muted-foreground)]">{JSON.stringify(memory.content, null, 2)}</pre></div>)}
          </div>
        </div>
      </section>

      <section id="catalog" className="card p-5">
        <h2 className="text-lg font-semibold text-[var(--foreground)]">内部专业 Agent 目录</h2>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">用户只与顶层 Agent 交互；专业 Agent 的版本和工具白名单可审计。</p>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {catalog?.agents.map((agent) => <div key={agent.key} className="card p-4"><p className="font-medium text-[var(--foreground)]">{humanizeAgent(agent.key)}</p><p className="mt-1 text-xs text-[var(--primary)]">v{agent.version}</p><p className="mt-3 text-sm leading-5 text-[var(--muted-foreground)]">{agent.description}</p><p className="mt-3 text-xs text-[var(--muted-foreground)]">{agent.tools.length} 个允许工具</p></div>)}
        </div>
      </section>
    </main>
  );
}

/** 渲染 ContextSelectors 组件，集中展示上下文选择器所需的数据、状态和操作入口。 */
function ContextSelectors({ products, stores, compact = false, disabled = false }: { products: Array<{ id: number; name: string }>; stores: Array<{ id: number; name: string }>; compact?: boolean; disabled?: boolean }) {
  return <div className={`grid gap-3 ${compact ? "" : "sm:grid-cols-2"}`}><select name="product_id" disabled={disabled} className="input h-10 w-full rounded-[10px] px-3 text-sm disabled:opacity-60"><option value="">选择商品上下文</option>{products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}</select><select name="store_id" disabled={disabled} className="input h-10 w-full rounded-[10px] px-3 text-sm disabled:opacity-60"><option value="">选择店铺上下文</option>{stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}</select></div>;
}

/** 渲染 ApprovalForm 组件，集中展示审批表单所需的数据、状态和操作入口。 */
function ApprovalForm({ approvalId, workspaceId, decision, draftOutput }: { approvalId: number; workspaceId: number; decision: "approve" | "reject"; draftOutput?: unknown }) {
  const approve = decision === "approve";
  return <form action={`/agent/approvals/${approvalId}/decision`} method="post" className="space-y-2"><input type="hidden" name="workspace_id" value={workspaceId} /><input type="hidden" name="decision" value={decision} />{approve ? <textarea name="edited_output" defaultValue={JSON.stringify(draftOutput ?? {}, null, 2)} className="input min-h-40 w-full rounded-[10px] bg-[var(--card)] px-3 py-2 font-mono text-xs" aria-label="编辑后批准的结构化输出" /> : null}<button className={`inline-flex h-9 items-center gap-2 rounded-[10px] px-3 text-sm font-medium ${approve ? "bg-gradient-success text-white shadow-[var(--shadow-btn)] hover:opacity-90" : "btn-ghost border border-[var(--danger)]/30 text-[var(--danger)] hover:bg-[var(--danger)]/10"}`}>{approve ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : <XCircle className="h-4 w-4" aria-hidden="true" />}{approve ? "编辑后批准并恢复" : "拒绝"}</button></form>;
}

/** 渲染 Metric 组件，集中展示指标所需的数据、状态和操作入口。 */
function Metric({ icon: Icon, label, value, gradient = "bg-gradient-primary" }: { icon: typeof Activity; label: string; value: number; gradient?: string }) {
  return <div className="card card-hover p-4"><div className="flex items-center justify-between gap-3"><p className="truncate text-sm text-[var(--muted-foreground)]">{label}</p><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] text-white shadow-[var(--shadow-btn)] ${gradient}`}><Icon className="h-4 w-4" aria-hidden="true" /></span></div><p className="mt-3 text-2xl font-semibold text-[var(--foreground)]">{value}</p></div>;
}

/** 渲染 JsonPanel 组件，集中展示json面板所需的数据、状态和操作入口。 */
function JsonPanel({ title, value }: { title: string; value: Record<string, unknown> }) {
  return <div className="rounded-[10px] bg-[var(--muted)] p-3"><p className="text-xs font-semibold text-[var(--muted-foreground)]">{title}</p><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--foreground)]">{Object.keys(value).length ? JSON.stringify(value, null, 2) : "暂无"}</pre></div>;
}

/** 渲染 EmptyText 组件，集中展示空状态文本所需的数据、状态和操作入口。 */
function EmptyText({ text }: { text: string }) { return <div className="rounded-[10px] border border-dashed border-[var(--border-strong)] px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">{text}</div>; }

/** 渲染 EmptyWorkspace 组件，集中展示空状态Workspace所需的数据、状态和操作入口。 */
function EmptyWorkspace() { return <div className="flex min-h-[60vh] items-center justify-center p-6"><div className="card max-w-lg p-8 text-center"><Bot className="mx-auto h-8 w-8 text-[var(--muted-foreground)]" /><h1 className="mt-4 text-xl font-semibold text-[var(--foreground)]">暂无可用 Workspace</h1><p className="mt-2 text-sm text-[var(--muted-foreground)]">请先运行数据库迁移并为当前用户创建 WorkspaceMembership。</p></div></div>; }

/** 渲染 AgentServiceError 组件，集中展示Agent服务错误所需的数据、状态和操作入口。 */
function AgentServiceError() { return <div className="flex min-h-[60vh] items-center justify-center p-6"><div className="card max-w-lg border-[var(--danger)]/30 p-8 text-center"><XCircle className="mx-auto h-8 w-8 text-[var(--danger)]" /><h1 className="mt-4 text-xl font-semibold text-[var(--foreground)]">Agent 服务暂不可用</h1><p className="mt-2 text-sm text-[var(--muted-foreground)]">请求未被伪装为空数据，请检查 API、权限或基础设施状态后重试。</p></div></div>; }

/** 渲染 ResultBanner 组件，集中展示结果提示横幅所需的数据、状态和操作入口。 */
function ResultBanner({ result }: { result: string }) {
  const failed = result === "failed";
  return <div className={`rounded-[10px] border px-4 py-3 text-sm ${failed ? "border-[var(--danger)]/30 bg-[var(--danger)]/10 text-[var(--danger)]" : "border-[var(--success)]/30 bg-[var(--success)]/10 text-[var(--success)]"}`}>{failed ? "操作失败，请检查输入和权限。" : "操作已提交，任务状态会持续更新。"}</div>;
}

/** 处理选择Workspace交互，维护必要状态并把副作用交给受控路由或后端 API。 */
function selectWorkspace(workspaces: Awaited<ReturnType<typeof getWorkspaces>>, requested?: string) {
  const requestedId = Number(requested);
  return workspaces.find((item) => item.workspace.id === requestedId) ?? workspaces[0] ?? null;
}

/** 格式化日期，统一空值、本地化和页面展示规则，避免各组件重复格式化。 */
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }

/** 转换显示名Agent，统一空值、本地化和页面展示规则，避免各组件重复格式化。 */
function humanizeAgent(key: string) { return key.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" "); }
