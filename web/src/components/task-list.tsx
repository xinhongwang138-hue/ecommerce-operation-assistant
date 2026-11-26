"use client";

import { XCircle } from "lucide-react";
import { useMemo, useState } from "react";

import { Progress, StatusBadge } from "@/components/ui";
import type { AgentTask } from "@/types";

const statusFilters = [
  { key: "all", label: "全部" },
  { key: "queued", label: "排队中" },
  { key: "running", label: "执行中" },
  { key: "awaiting_approval", label: "待审批" },
  { key: "completed", label: "已完成" },
  { key: "failed", label: "失败" },
  { key: "cancelled", label: "已取消" },
] as const;

const statusLabels: Record<AgentTask["status"], string> = {
  queued: "排队中",
  running: "执行中",
  awaiting_approval: "待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const statusTone: Record<AgentTask["status"], "success" | "warning" | "danger" | "info" | "muted" | "running"> = {
  queued: "muted",
  running: "running",
  awaiting_approval: "warning",
  completed: "success",
  failed: "danger",
  cancelled: "muted",
};

const progressByStatus: Record<AgentTask["status"], number> = {
  queued: 15,
  running: 60,
  awaiting_approval: 85,
  completed: 100,
  failed: 100,
  cancelled: 0,
};

/** 渲染 AgentTaskList 组件，任务中心支持状态筛选、进度条与取消操作。 */
export function AgentTaskList({
  tasks,
  workspaceId,
  canEdit,
}: {
  tasks: AgentTask[];
  workspaceId: number;
  canEdit: boolean;
}) {
  const [filter, setFilter] = useState<string>("all");
  const visible = useMemo(
    () => (filter === "all" ? tasks : tasks.filter((task) => task.status === filter)),
    [tasks, filter],
  );

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {statusFilters.map((item) => {
          const active = filter === item.key;
          const count = item.key === "all" ? tasks.length : tasks.filter((task) => task.status === item.key).length;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setFilter(item.key)}
              className={`inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-medium transition ${
                active
                  ? "bg-gradient-primary text-white shadow-[var(--shadow-btn)]"
                  : "border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:border-[var(--primary)]/40 hover:text-[var(--primary)]"
              }`}
            >
              {item.label}
              <span className={`rounded-full px-1.5 text-[10px] ${active ? "bg-white/20" : "bg-[var(--muted)]"}`}>{count}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-5 space-y-3">
        {visible.length === 0 ? (
          <div className="rounded-[10px] border border-dashed border-[var(--border-strong)] px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
            该状态下暂无 Agent Task。
          </div>
        ) : (
          visible.map((task) => (
            <details key={task.id} className="card p-4">
              <summary className="cursor-pointer list-none">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="font-medium text-[var(--foreground)]">#{task.id} {task.title}</p>
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {task.workflow_key} · v{task.workflow_version} · {formatDate(task.created_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge label={statusLabels[task.status]} tone={statusTone[task.status]} />
                  </div>
                </div>
                {["queued", "running", "awaiting_approval"].includes(task.status) ? (
                  <div className="mt-3 flex items-center gap-2">
                    <Progress value={progressByStatus[task.status]} className="max-w-56" />
                    <span className="text-[11px] text-[var(--muted-foreground)]">{progressByStatus[task.status]}%</span>
                  </div>
                ) : null}
              </summary>
              <div className="mt-4 grid gap-4 border-t border-[var(--border)] pt-4 lg:grid-cols-2">
                <JsonPanel title="上下文" value={task.context_scope} />
                <JsonPanel title="结果摘要" value={task.result_summary} />
              </div>
              {task.error ? <p className="mt-3 text-sm text-[var(--danger)]">{task.error}</p> : null}
              {["queued", "running", "awaiting_approval"].includes(task.status) && canEdit ? (
                <form action={`/agent/tasks/${task.id}/cancel`} method="post" className="mt-3">
                  <input type="hidden" name="workspace_id" value={workspaceId} />
                  <button className="inline-flex h-8 items-center gap-2 rounded-[8px] border border-[var(--danger)]/30 bg-[var(--card)] px-3 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger)]/10">
                    <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                    取消任务
                  </button>
                </form>
              ) : null}
            </details>
          ))
        )}
      </div>
    </div>
  );
}

/** 渲染 JsonPanel 组件，JSON 预览面板。 */
function JsonPanel({ title, value }: { title: string; value: Record<string, unknown> }) {
  return (
    <div className="rounded-[10px] bg-[var(--muted)] p-3">
      <p className="text-xs font-semibold text-[var(--muted-foreground)]">{title}</p>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--foreground)]">
        {Object.keys(value).length ? JSON.stringify(value, null, 2) : "暂无"}
      </pre>
    </div>
  );
}

/** 格式化日期，客户端展示统一口径。 */
function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
