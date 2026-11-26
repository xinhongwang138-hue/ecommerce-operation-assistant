import Link from "next/link";
import { Headset, MessageSquare, ShieldQuestion, Ticket, Users } from "lucide-react";

import { CsPanel } from "@/components/cs-panel";
import { StatusBadge } from "@/components/ui";
import { getCsFaq, getCsSummary, getCsTickets } from "@/lib/api";

const TRACK_META: Record<string, { label: string; hint: string; tone: "success" | "info" | "danger" | "warning" }> = {
  faq: { label: "FAQ 直答", hint: "确定性知识，不经过检索排序", tone: "success" },
  retrieval: { label: "资料检索", hint: "查资料才答得上，带引用", tone: "info" },
  refuse: { label: "拒答", hint: "没有依据就不猜，给转人工入口", tone: "warning" },
  handoff: { label: "转人工", hint: "投诉/要求人工/情绪负面，建工单", tone: "danger" },
};

/**
 * AI 电商客服（P01）：三轨道分流 + 命中不足即拒答 + 转人工工单。
 *
 * 首屏把分流分布摆在最前：一眼能看出"多少问题是我们真的答不了的"。
 * 对话在客户端按需触发，首屏不依赖它。
 */
export default async function CsPage() {
  const [summary, tickets, faqItems] = await Promise.all([
    getCsSummary(),
    getCsTickets(10),
    getCsFaq(),
  ]);

  const counts = new Map((summary?.tracks ?? []).map((item) => [item.track, item.count]));
  const totalTracks = (summary?.tracks ?? []).reduce((sum, item) => sum + item.count, 0);

  const cards = [
    { label: "会话", value: `${summary?.conversations ?? 0}`, hint: "已落库，可被 P09 质检", icon: Users },
    { label: "消息", value: `${summary?.messages ?? 0}`, hint: "客户 + 客服两侧", icon: MessageSquare },
    {
      label: "转人工工单",
      value: `${summary?.handoff_tickets ?? 0}`,
      hint: `待处理 ${summary?.open_tickets ?? 0}`,
      icon: Ticket,
    },
    { label: "内置 FAQ", value: `${summary?.faq_items ?? 0}`, hint: "条确定性知识", icon: ShieldQuestion },
  ];

  const principles = [
    {
      title: "宁可拒答，也不编",
      body: "客服场景里一个自信的错答案比「我没查到」贵得多——客户会照着错答案退货、索赔、发差评。所以拒答是正常结果，并且会说明最接近的相似度是多少。",
    },
    {
      title: "每条回复都能指出依据",
      body: "FAQ 命中就说是哪一条；检索命中就给文档与片段、相似度；转人工就说清为什么。用户问「你凭什么这么说」时，系统要答得出来。",
    },
    {
      title: "会话必须落库",
      body: "回复完就丢的对话，事后无法复盘、无法考核，也无法变成训练数据。P01 写的会话，P09 直接拿去质检——这是同一份数据被两个模块消费。",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <Headset className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">AI Customer Service</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">AI 电商客服</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              提问先进三轨道：该转人工的先转、能直答的直答、要查资料的走混合检索；
              检索命中不足就拒答，不猜。会话会落库，作为销售考核的质检输入。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge label={`${totalTracks} 条已分流`} tone={totalTracks > 0 ? "info" : "muted"} />
          <span className="text-[var(--muted-foreground)]">
            拒答 {counts.get("refuse") ?? 0} 条 · 转人工 {counts.get("handoff") ?? 0} 条
          </span>
          <Link className="text-[var(--primary)] underline" href="/qa">
            质检结果看销售考核
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

      <CsPanel tickets={tickets} />

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">分流分布</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            轨道是"当时判的"，不从历史消息重算——规则改了，统计不该跟着变
          </p>
          <ul className="mt-3 flex flex-col gap-3">
            {(["faq", "retrieval", "refuse", "handoff"] as const).map((track) => {
              const count = counts.get(track) ?? 0;
              const meta = TRACK_META[track];
              const ratio = totalTracks > 0 ? Math.round((count / totalTracks) * 100) : 0;
              return (
                <li key={track}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <StatusBadge label={meta.label} tone={meta.tone} />
                      <span className="text-[11px] text-[var(--muted-foreground)]">{meta.hint}</span>
                    </span>
                    <span className="text-[11px] text-[var(--muted-foreground)]">
                      {count} · {ratio}%
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
                    <div
                      className="h-full rounded-full bg-[var(--primary)]"
                      style={{ width: `${ratio}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="card overflow-hidden">
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold text-[var(--foreground)]">内置 FAQ 清单</h2>
            <p className="text-[11px] text-[var(--muted-foreground)]">
              答得上来什么，应该能直接看到——否则"拒答"会显得很随机
            </p>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {faqItems.map((item) => (
              <li key={item.key} className="px-4 py-2.5">
                <p className="flex items-center gap-2 text-xs font-medium text-[var(--foreground)]">
                  <span className="rounded-full bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
                    {item.category}
                  </span>
                  {item.question}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                  {item.answer}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </section>

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
