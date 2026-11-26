import { TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Progress as ShadcnProgress } from "@/components/ui/progress";
import { Skeleton as ShadcnSkeleton } from "@/components/ui/skeleton";

/** 渲染 Skeleton 组件（shadcn/ui 版），用于客户端异步区域加载占位。 */
export function Skeleton({ className = "" }: { className?: string }) {
  return <ShadcnSkeleton className={className} aria-hidden="true" />;
}

/** 渲染 SkeletonCard 组件，卡片形态的加载占位。 */
export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <Card className="p-5">
      <Skeleton className="h-4 w-1/3" />
      <div className="mt-4 space-y-2.5">
        {Array.from({ length: lines }).map((_, index) => (
          <Skeleton key={index} className="h-3.5 w-full" />
        ))}
      </div>
    </Card>
  );
}

/** 渲染 Progress 组件（shadcn/ui 版），展示任务进度。 */
export function Progress({ value, className = "" }: { value: number; className?: string }) {
  const percent = Math.min(100, Math.max(0, value));
  return (
    <ShadcnProgress
      value={percent}
      className={cn("h-1.5", className)}
      aria-label="进度"
    />
  );
}

/** 渲染 StatusBadge 组件（shadcn Badge + 渐变组合类），渐变风格状态徽章。 */
export function StatusBadge({
  label,
  tone = "muted",
}: {
  label: string;
  tone?: "success" | "warning" | "danger" | "info" | "muted" | "running";
}) {
  const toneClass = `badge-gradient ${
    tone === "success"
      ? "badge-success"
      : tone === "warning"
        ? "badge-warning"
        : tone === "danger"
          ? "badge-danger"
          : tone === "info"
            ? "badge-info"
            : tone === "running"
              ? "badge-running"
              : "badge-muted"
  }`;
  return <Badge className={toneClass}>{label}</Badge>;
}

/** 渲染 StatCard 组件（shadcn Card 容器），渐变图标 + 指标样式的统计卡片。 */
export function StatCard({
  label,
  value,
  icon: Icon,
  gradient = "bg-gradient-primary",
  hint,
}: {
  label: string;
  value: number | string;
  icon: React.ComponentType<{ className?: string }>;
  gradient?: string;
  hint?: string;
}) {
  return (
    <Card className="card-hover p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-[var(--foreground)]">{value}</p>
          {hint ? <p className="mt-1 truncate text-[11px] text-[var(--muted-foreground)]">{hint}</p> : null}
        </div>
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] text-white shadow-[var(--shadow-btn)] ${gradient}`}>
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      </div>
    </Card>
  );
}

/** 渲染 EmptyIcon 组件，StatCard 默认图标兜底。 */
export function DefaultStatIcon() {
  return <TrendingUp className="h-5 w-5" aria-hidden="true" />;
}
