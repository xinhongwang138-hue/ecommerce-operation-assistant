import { ArrowRight, Bell } from "lucide-react";
import Link from "next/link";

import { getAlertSummary } from "@/lib/api";

/** 首页待办横幅：只在有待处理事项时展示，点击进入 /alerts。 */
export async function AlertOverviewCard() {
  const summary = await getAlertSummary();
  if (!summary || summary.total === 0) {
    return null;
  }

  const active = summary.groups.filter((group) => group.count > 0);

  return (
    <Link
      href="/alerts"
      className="card flex items-center justify-between gap-4 border-amber-200 bg-amber-50/60 p-4 transition hover:bg-amber-50"
    >
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-amber-600 text-white">
          <Bell className="h-5 w-5" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-950">
            今日待处理 {summary.total.toLocaleString("en-US")} 项
          </p>
          <p className="mt-0.5 truncate text-xs text-slate-600">
            {active.map((group) => `${group.title} ${group.count}`).join(" · ")}
          </p>
        </div>
      </div>
      <ArrowRight className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
    </Link>
  );
}
