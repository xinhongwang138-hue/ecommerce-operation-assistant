import Link from "next/link";
import { AlertTriangle, GitBranch, ShieldCheck, Sparkles } from "lucide-react";

import { Nl2SqlPanel } from "@/components/nl2sql-panel";
import { StatusBadge } from "@/components/ui";
import { getDataPlatformOverview, getNl2SqlExamples } from "@/lib/api";

/** 问数页：首屏只取"仓是否就绪"和示例，真正的问答在客户端按需触发。 */
export default async function AskPage() {
  const [examples, overview] = await Promise.all([getNl2SqlExamples(), getDataPlatformOverview()]);
  const ready = overview?.ready ?? false;

  const steps = [
    {
      icon: Sparkles,
      title: "问题 → SQL",
      body: "模型只看到表结构、列类型和已定义口径，看不到数据本身。它输出的是一条 SELECT。",
    },
    {
      icon: ShieldCheck,
      title: "先过护栏，再执行",
      body: "与手敲 SQL 完全相同的边界：单条 SELECT/WITH、表白名单、最多 500 行、只读连接。",
    },
    {
      icon: GitBranch,
      title: "说得清怎么算的",
      body: "SQL 和口径一起返回。数对不对可以查，口径对不对只能靠写清楚。",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <Sparkles className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">NL2SQL</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">问数</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              用中文问经营问题，得到一段能看懂的 SQL 和一张表。模型是写 SQL 的，不是查数的——
              它写出来的东西必须自己过一遍只读护栏，才轮得到执行。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge label={ready ? "仓已就绪" : "尚未抽取"} tone={ready ? "success" : "warning"} />
          {overview?.run ? (
            <span className="text-[var(--muted-foreground)]">
              数据截至 {overview.summary?.window_end ?? "—"} · 上次跑批{" "}
              {new Date(overview.run.finished_at).toLocaleString("zh-CN")}
            </span>
          ) : (
            <span className="text-[var(--muted-foreground)]">还没跑过批，问数会没有表可查</span>
          )}
        </div>

        {ready ? null : (
          <div className="mt-4 flex items-start gap-2 rounded-[10px] border border-[var(--warning)] bg-[var(--warning-soft)] px-3 py-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--warning)]" aria-hidden="true" />
            <p className="text-xs leading-relaxed text-[var(--foreground)]">
              数据仓是空的，问数会返回「数据仓尚未构建」。先去{" "}
              <Link className="font-medium text-[var(--primary)] underline" href="/data-platform">
                数据中台
              </Link>{" "}
              点一次抽取，再回来提问。
            </p>
          </div>
        )}
      </header>

      <section className="grid gap-4 md:grid-cols-3">
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <div key={step.title} className="card p-4">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] bg-[var(--primary-soft)] text-[var(--primary)]">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </div>
                <p className="text-sm font-semibold text-[var(--foreground)]">{step.title}</p>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted-foreground)]">{step.body}</p>
            </div>
          );
        })}
      </section>

      <Nl2SqlPanel examples={examples} />

      <p className="text-[11px] leading-relaxed text-[var(--muted-foreground)]">
        两次都生成不出可执行的 SQL 时，接口返回 422 并带上每一轮被拒的原因——这不是错误页，
        是给你看的调试信息：原因里会写清是「表不在白名单」「列名不存在」还是「执行报错」。
      </p>
    </main>
  );
}
