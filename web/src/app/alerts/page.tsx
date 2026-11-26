import { AlertTriangle, ArrowRight, Bell, CheckCircle2, Package, Radar } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";

import { getAlertSummary } from "@/lib/api";
import type { AlertGroup } from "@/types";

const GROUP_META: Record<string, { icon: LucideIcon; tone: string }> = {
  low_stock: { icon: Package, tone: "bg-amber-600" },
  competitor_change: { icon: Radar, tone: "bg-sky-700" },
  agent_failed: { icon: AlertTriangle, tone: "bg-rose-600" },
  approval_pending: { icon: Bell, tone: "bg-violet-600" },
};

/** 待办中心：把散落在各页的风险收口成一页可点击的清单。 */
export default async function AlertsPage() {
  const summary = await getAlertSummary();

  return (
    <main className="flex flex-col gap-6">
      <header className="flex flex-col gap-2 border-b border-slate-200 pb-5">
        <p className="mt-5 text-sm font-medium text-[var(--primary)]">Alerts</p>
        <h1 className="text-2xl font-semibold tracking-normal text-slate-950 sm:text-3xl">待办中心</h1>
        <p className="text-sm text-slate-500">把低库存、竞品变化、失败任务和待审批收口到一处</p>
      </header>

      {!summary ? (
        <div className="flex flex-col gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p>待办读取失败：可能未登录、登录已过期，或后端服务不可用。</p>
          <Link href="/login" className="w-fit font-medium underline underline-offset-4">
            前往登录
          </Link>
        </div>
      ) : summary.total === 0 ? (
        <div className="card flex flex-col items-center gap-3 p-10 text-center">
          <CheckCircle2 className="h-10 w-10 text-emerald-600" aria-hidden="true" />
          <p className="text-base font-semibold text-slate-950">今天没有待处理事项</p>
          <p className="text-sm text-slate-500">低库存、竞品变化、失败任务和待审批目前都是 0。</p>
        </div>
      ) : (
        <>
          <section className="card flex items-center gap-3 p-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-gradient-primary text-white">
              <Bell className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm text-slate-500">待处理合计</p>
              <p className="text-2xl font-semibold text-slate-950">
                {summary.total.toLocaleString("en-US")}
              </p>
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            {summary.groups.map((group) => (
              <AlertGroupCard key={group.key} group={group} />
            ))}
          </div>
        </>
      )}
    </main>
  );
}

/** 渲染单类待办卡片：展示数量与前若干条明细。 */
function AlertGroupCard({ group }: { group: AlertGroup }) {
  const meta = GROUP_META[group.key] ?? { icon: AlertTriangle, tone: "bg-slate-700" };
  const Icon = meta.icon;

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-white ${meta.tone}`}>
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-slate-950">{group.title}</h2>
            <p className="mt-1 text-sm text-slate-500">{group.hint}</p>
          </div>
        </div>
        <span className="rounded-md bg-slate-100 px-2 py-1 text-sm font-semibold text-slate-700">
          {group.count}
        </span>
      </div>

      {group.items.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">暂无明细。</p>
      ) : (
        <div className="mt-4 divide-y divide-slate-100">
          {group.items.map((item) => (
            <Link
              key={`${group.key}-${item.id}`}
              href={alertItemHref(group.key, item.id)}
              className="flex items-center justify-between gap-3 py-2.5 text-sm hover:text-[var(--primary)]"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium text-slate-900">{item.title}</span>
                {item.subtitle ? (
                  <span className="mt-0.5 block truncate text-xs text-slate-500">{item.subtitle}</span>
                ) : null}
              </span>
              <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
            </Link>
          ))}
        </div>
      )}

      {group.count > group.items.length ? (
        <p className="mt-3 text-xs text-slate-500">
          另有 {group.count - group.items.length} 条未展示
        </p>
      ) : null}
    </section>
  );
}

/** 按待办类型拼接详情链接：库存/竞品回到商品页，任务/审批回到 Agent 中心。 */
function alertItemHref(key: string, id: number): string {
  if (key === "low_stock" || key === "competitor_change") {
    return `/products/${id}?tab=overview`;
  }
  return "/agent";
}
