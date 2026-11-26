"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Progress, StatusBadge } from "@/components/ui";
import type { GenerationJob } from "@/types";

const statusFilters = [
  { key: "all", label: "全部" },
  { key: "pending", label: "排队中" },
  { key: "running", label: "生成中" },
  { key: "succeeded", label: "已完成" },
  { key: "failed", label: "失败" },
  { key: "cancelled", label: "已取消" },
] as const;

const statusTone: Record<GenerationJob["status"], "success" | "warning" | "danger" | "info" | "muted" | "running"> = {
  pending: "muted",
  running: "running",
  succeeded: "success",
  failed: "danger",
  cancelled: "muted",
};

const statusLabels: Record<GenerationJob["status"], string> = {
  pending: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const progressByStatus: Record<GenerationJob["status"], number> = {
  pending: 15,
  running: 65,
  succeeded: 100,
  failed: 100,
  cancelled: 0,
};

/** 渲染 GenerationJobList 组件，任务列表支持状态筛选、进度条与操作入口。 */
export function GenerationJobList({
  jobs,
  productId,
  canEdit,
  totalCount,
}: {
  jobs: GenerationJob[];
  productId: number;
  canEdit: boolean;
  totalCount: number;
}) {
  const [filter, setFilter] = useState<string>("all");
  const visible = useMemo(
    () => (filter === "all" ? jobs : jobs.filter((job) => job.status === filter)),
    [jobs, filter],
  );

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {statusFilters.map((item) => {
          const active = filter === item.key;
          const count = item.key === "all" ? totalCount : jobs.filter((job) => job.status === item.key).length;
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

      {visible.length === 0 ? (
        <div className="mt-5 rounded-[10px] border border-dashed border-[var(--border-strong)] px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
          该状态下暂无生成任务。
        </div>
      ) : (
        <div className="mt-5 overflow-hidden rounded-[10px] border border-[var(--border)]">
          <div className="grid grid-cols-[80px_90px_110px_1fr_160px] gap-3 bg-[var(--card-muted)] px-3 py-2 text-xs font-semibold text-[var(--muted-foreground)]">
            <span>ID</span>
            <span>类型</span>
            <span>状态</span>
            <span>结果 / 错误</span>
            <span>操作</span>
          </div>
          <div className="divide-y divide-[var(--border)]">
            {visible.map((job) => (
              <div
                key={job.id}
                className="grid grid-cols-[80px_90px_110px_1fr_160px] items-center gap-3 px-3 py-3 text-sm"
              >
                <span className="font-medium text-[var(--foreground)]">#{job.id}</span>
                <span className="text-[var(--muted-foreground)]">{job.kind === "video_asset" ? "视频" : "图片"}</span>
                <div className="flex flex-col gap-1.5">
                  <StatusBadge label={statusLabels[job.status]} tone={statusTone[job.status]} />
                  {job.status === "running" || job.status === "pending" ? (
                    <Progress value={progressByStatus[job.status]} className="w-24" />
                  ) : null}
                </div>
                <div className="min-w-0">
                  {job.status === "succeeded" && job.result?.url ? (
                    <a
                      href={typeof job.result?.url === "string" ? absoluteMediaUrl(job.result.url) : "#"}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[var(--primary)] hover:underline"
                    >
                      打开生成结果
                    </a>
                  ) : job.error ? (
                    <p className="line-clamp-2 text-xs leading-5 text-[var(--danger)]">{job.error}</p>
                  ) : (
                    <p className="text-xs text-[var(--muted-foreground)]">
                      {job.started_at ? `开始于 ${job.started_at}` : "等待后台执行"}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={`/products/${productId}/generation-jobs/${job.id}`}
                    className="inline-flex h-8 items-center rounded-[8px] border border-[var(--border)] bg-[var(--card)] px-3 text-xs font-medium text-[var(--foreground)] hover:border-[var(--primary)]/40 hover:text-[var(--primary)]"
                  >
                    详情
                  </Link>
                  {canEdit && job.status === "failed" ? (
                    <form action={`/products/${productId}/generation-jobs/${job.id}/retry`} method="post">
                      <input type="hidden" name="tab" value="jobs" />
                      <button
                        type="submit"
                        className="h-8 rounded-[8px] bg-gradient-primary px-3 text-xs font-medium text-white shadow-[var(--shadow-btn)] hover:opacity-90"
                      >
                        重试
                      </button>
                    </form>
                  ) : canEdit && (job.status === "pending" || job.status === "running") ? (
                    <form action={`/products/${productId}/generation-jobs/${job.id}/cancel`} method="post">
                      <input type="hidden" name="tab" value="jobs" />
                      <button
                        type="submit"
                        className="h-8 rounded-[8px] border border-[var(--danger)]/30 bg-[var(--card)] px-3 text-xs font-medium text-[var(--danger)] hover:bg-[var(--danger)]/10"
                      >
                        取消
                      </button>
                    </form>
                  ) : (
                    <span className="text-xs text-[var(--muted-foreground)]">-</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** 绝对地址媒体URL，客户端展示时复用与服务端一致的空值边界。 */
function absoluteMediaUrl(url: string): string {
  // 后端媒体路径需要登录态，交给同源代理转发。
  if (url.startsWith("/uploads/")) {
    return `/api/media/${url.slice("/uploads/".length)}`;
  }
  try {
    return new URL(url, window.location.origin).href;
  } catch {
    return url;
  }
}
