import Link from "next/link";
import { CheckCircle2, Clock, Scissors, Video } from "lucide-react";

import { LivePanel } from "@/components/live-panel";
import { StatusBadge } from "@/components/ui";
import { getLiveSessions, getLiveSummary } from "@/lib/api";

/**
 * 直播切片（P08）。
 *
 * 首屏就说清边界：这里是"挑片段"，不是"出成片"。把挑片包装成自动剪辑，
 * 用户点开发现只有时间码，比什么都不做更伤信任。
 */
export default async function LivePage() {
  const [summary, sessions] = await Promise.all([getLiveSummary(), getLiveSessions(10)]);

  const cards = [
    { label: "直播场次", value: `${summary?.sessions ?? 0}`, hint: "已转写", icon: Video },
    { label: "转写句数", value: `${summary?.segments ?? 0}`, hint: "每句带秒级时间码", icon: Clock },
    { label: "切片候选", value: `${summary?.clips ?? 0}`, hint: `均分 ${summary?.average_score ?? 0}`, icon: Scissors },
    { label: "已通过", value: `${summary?.approved ?? 0}`, hint: `已驳回 ${summary?.rejected ?? 0}`, icon: CheckCircle2 },
  ];

  const principles = [
    {
      title: "只挑片段，不出成片",
      body: "产出是「第几秒到第几秒、为什么选它」。视频裁剪交给下游工具——把给建议包装成已生成，用户点开会发现什么都没有。",
    },
    {
      title: "每条候选都说清理由",
      body: "卖点密度、开场钩子、行动号召、时长合规，四项各自计分并逐条列出命中词。没有理由的候选在剪辑台上等于噪声。",
    },
    {
      title: "重挑保留人工结论",
      body: "改了阈值再跑一遍，只清掉「待定」的候选。已通过/已驳回的留着——人的判断成本比机器高，不能因为调参就丢掉。",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <Scissors className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">Live Clip Finder</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">直播切片</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              吃进带时间码的直播转写，按卖点密度、开场钩子、行动号召和时长挑出值得单独发的片段。
              每个候选都给起止秒数与命中理由，剪辑师拿这组时间码去出成片。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            label={`${summary?.sessions ?? 0} 场已转写`}
            tone={(summary?.sessions ?? 0) > 0 ? "info" : "muted"}
          />
          <span className="text-[var(--muted-foreground)]">
            候选 {summary?.clips ?? 0} · 通过 {summary?.approved ?? 0}
          </span>
          <Link className="text-[var(--primary)] underline" href="/qa">
            切片表现可以回流到销售考核
          </Link>
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="card p-4">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-[var(--muted-foreground)]">{card.label}</p>
                <Icon className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden="true" />
              </div>
              <p className="mt-2 text-2xl font-semibold tracking-tight text-[var(--foreground)]">
                {card.value}
              </p>
              <p className="mt-1 truncate text-[11px] text-[var(--muted-foreground)]">{card.hint}</p>
            </div>
          );
        })}
      </section>

      <LivePanel sessions={sessions} />

      <section className="grid gap-4 md:grid-cols-3">
        {principles.map((item) => (
          <div key={item.title} className="card p-4">
            <p className="text-sm font-semibold text-[var(--foreground)]">{item.title}</p>
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted-foreground)]">{item.body}</p>
          </div>
        ))}
      </section>
    </main>
  );
}
