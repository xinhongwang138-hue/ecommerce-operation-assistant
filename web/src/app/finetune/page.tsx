import { Cpu, Database, FlaskConical, Layers } from "lucide-react";

import { FinetunePanel } from "@/components/finetune-panel";
import { StatusBadge } from "@/components/ui";
import { getFinetuneDatasets, getFinetuneJobs, getFinetuneSummary } from "@/lib/api";

/**
 * 模型微调平台（P10）。
 *
 * 页面第一段就把边界摆出来：这里不做训练。做的是训练前后没人愿意手工做的那部分——
 * 样本回流、去重分层、导出 JSONL、把"到底有没有变好"记成一条能查的结论。
 */
export default async function FinetunePage() {
  const [summary, datasets, jobs] = await Promise.all([
    getFinetuneSummary(),
    getFinetuneDatasets(20),
    getFinetuneJobs(20),
  ]);

  const cards = [
    { label: "数据集", value: `${summary?.datasets ?? 0}`, hint: "从业务模块回流", icon: Database },
    { label: "样本", value: `${summary?.samples ?? 0}`, hint: "已去重、分质量层", icon: Layers },
    { label: "训练任务", value: `${summary?.jobs ?? 0}`, hint: "只登记，不执行", icon: Cpu },
    {
      label: "评测记录",
      value: `${jobs.filter((item) => item.eval_summary?.cases).length}`,
      hint: "同一批题的 base / tuned",
      icon: FlaskConical,
    },
  ];

  const principles = [
    {
      title: "不训练，只准备与记账",
      body: "没有 GPU 也没有训练服务，假装能训练就是假功能。这里做的是：把真实记录捞出来、去重、分层、转成 JSONL；把训练后的对比记成台账。",
    },
    {
      title: "每个样本可追溯",
      body: "每条样本带 source + source_ref，指回 qa_session:12 这样的原始记录。训练数据说不清来源，就没法回答「模型为什么会这么说」。",
    },
    {
      title: "待修样本不进训练集",
      body: "低分会话和红线会话标成待修，导出时排除。把它们直接拿去训，等于教模型学错误的回答——先人工改完再用。",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <Cpu className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">Model Finetune</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">
              模型微调平台
            </h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              销售考核的低分会话、AI 客服的拒答与转人工会话，都是最贴近真实语料的训练素材。
              这里把它们去重、分层、导出成 JSONL，并把训练前后的对比记成台账。
              <span className="text-[var(--foreground)]">本平台不执行训练。</span>
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            label={`${summary?.samples ?? 0} 条样本`}
            tone={(summary?.samples ?? 0) > 0 ? "info" : "muted"}
          />
          <span className="text-[var(--muted-foreground)]">
            {(summary?.by_quality ?? [])
              .map((row) => `${row.title} ${row.count}`)
              .join(" · ") || "还没有样本"}
          </span>
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

      <FinetunePanel datasets={datasets} jobs={jobs} />

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
