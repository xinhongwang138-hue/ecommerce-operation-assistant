import Link from "next/link";
import { BookOpen, Layers, Scissors, ShieldCheck } from "lucide-react";

import { RagPanel } from "@/components/rag-panel";
import { StatusBadge } from "@/components/ui";
import { getRagDocuments, getRagStats } from "@/lib/api";

const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 0 });

/** 自建知识库：首屏渲染统计与文档清单，检索与入库在客户端按需触发。 */
export default async function RagPage() {
  const [documents, stats] = await Promise.all([getRagDocuments(), getRagStats()]);

  const steps = [
    {
      icon: Scissors,
      title: "按句切分，保留字符区间",
      body: "不按固定 500 字硬切——硬切会把一句话劈两半，引用读起来像乱码。切片记录 char_start / char_end。",
    },
    {
      icon: Layers,
      title: "两路召回，RRF 融合",
      body: "BM25 看字面，向量看意思。两者错法不同，所以融合而不是二选一；RRF 只看名次，免疫量纲。",
    },
    {
      icon: ShieldCheck,
      title: "没有向量也能用",
      body: "向量端点失败或没配时，检索退化成纯词法并在页面上说明。降级要如实说，不能假装一切正常。",
    },
  ];

  const cards = [
    { label: "文档", value: `${stats?.documents ?? 0}`, hint: "篇" },
    { label: "切片", value: `${stats?.chunks ?? 0}`, hint: "片" },
    {
      label: "向量覆盖率",
      value: stats ? percent.format(stats.embedding_coverage) : "—",
      hint: stats?.embedding_model ?? "未配置",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <BookOpen className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">Self-hosted RAG</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">知识库</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              原文入库、切片、检索、引用全部在本仓完成，不依赖外部解析服务。检索命中会告诉你
              来自哪篇文档的第几片、对应原文的第几个字——引用要能核对，否则不叫引用。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            label={stats?.embedding_configured ? "混合召回" : "仅词法召回"}
            tone={stats?.embedding_configured ? "success" : "warning"}
          />
          <span className="text-[var(--muted-foreground)]">
            {stats?.embedded_chunks ?? 0} / {stats?.chunks ?? 0} 片带向量
          </span>
          <Link className="text-[var(--primary)] underline" href="/knowledge">
            需要上传 PDF / 走外部解析？用掌柜智库
          </Link>
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-3">
        {cards.map((card) => (
          <div key={card.label} className="card p-4">
            <p className="text-xs font-medium text-[var(--muted-foreground)]">{card.label}</p>
            <p className="mt-2 text-2xl font-semibold tracking-tight text-[var(--foreground)]">
              {card.value}
            </p>
            <p className="mt-1 truncate text-[11px] text-[var(--muted-foreground)]">{card.hint}</p>
          </div>
        ))}
      </section>

      <RagPanel documents={documents} stats={stats} />

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

      <p className="text-[11px] leading-relaxed text-[var(--muted-foreground)]">
        与「掌柜智库」的分工：PDF / 图片 / OCR 那些脏活仍交给外部服务（
        <Link className="text-[var(--primary)] underline" href="/knowledge">
          /knowledge
        </Link>
        ）；已经能直接切分的文本走这里。两条链路并存，外部服务不可用时知识库不会整体失效。
      </p>
    </main>
  );
}
