import Link from "next/link";
import { AlertTriangle, FileCheck2, FileSignature, ListChecks, ScrollText } from "lucide-react";

import { ContractPanel } from "@/components/contract-panel";
import { StatusBadge } from "@/components/ui";
import { getContractScoring, getContracts, getContractSummary } from "@/lib/api";

const LEVEL_META: Record<string, { label: string; tone: "success" | "warning" | "danger" }> = {
  low: { label: "低风险", tone: "success" },
  medium: { label: "中风险", tone: "warning" },
  high: { label: "高风险", tone: "danger" },
};

const RULE_TITLES: Record<string, string> = {
  payment_days_too_long: "账期过长",
  deposit_too_high: "定金比例过高",
  penalty_too_high: "违约金过高",
  unilateral_termination: "单方解除权",
  auto_renewal_without_notice: "自动续约无通知期",
  unlimited_liability: "赔偿不设上限",
  foreign_jurisdiction: "境外管辖",
  missing_dispute_clause: "缺争议解决条款",
  missing_acceptance_clause: "缺验收标准",
  missing_breach_clause: "缺违约责任",
  semantic_risk: "语义风险（模型）",
};

/**
 * 合同审批（P05）：上传 → 条款抽取 → 规则打分 → 语义复核 → 留痕审批。
 *
 * 首屏把「分数怎么算出来的」摆在最上面。一个不给口径的风险分，在审批会上
 * 一定会被问"凭什么"，然后整份报告就没人再看了。
 */
export default async function ContractsPage() {
  const [summary, contracts, scoring] = await Promise.all([
    getContractSummary(),
    getContracts(20),
    getContractScoring(),
  ]);

  const cards = [
    { label: "合同", value: `${summary?.contracts ?? 0}`, hint: "已入库", icon: ScrollText },
    {
      label: "待语义复核",
      value: `${summary?.pending_semantic ?? 0}`,
      hint: "规则已跑完，模型还没读",
      icon: ListChecks,
    },
    { label: "高风险", value: `${summary?.high_risk ?? 0}`, hint: "≥50 分", icon: AlertTriangle },
    { label: "审批记录", value: `${summary?.approvals ?? 0}`, hint: "只追加，不改写", icon: FileSignature },
  ];

  const levelRows = [
    { key: "high", ...LEVEL_META.high },
    { key: "medium", ...LEVEL_META.medium },
    { key: "low", ...LEVEL_META.low },
  ];

  const principles = [
    {
      title: "规则和模型分开记",
      body: "规则命中的可以摊开争论「这条规则对不对」；模型读出来的只能给理由，不能复现。混在一起，整份报告的可信度就会掉到模型水平。",
    },
    {
      title: "没有原句的风险不进报告",
      body: "每条都要给出触发它的那一句原话。指不出出处，审批人只能选择全信或全不信，两者都不是好结果。模型编的引用会被程序直接丢掉。",
    },
    {
      title: "不替人做通过/不通过",
      body: "系统只出风险清单，签不签是人签的。唯一的要求是：高风险合同点通过必须写理由——不是拦住你，是让签字有据可查。",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <FileCheck2 className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">Contract Review</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">合同审批</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              上传合同 → 按行首编号切条款 → 规则查可量化的风险（账期、比例、缺失条款）→
              模型补规则读不出的语义风险 → 逐条留痕审批。每条风险都能点回原句。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            label={`${summary?.reviewed ?? 0} / ${summary?.contracts ?? 0} 已复核`}
            tone={summary && summary.reviewed > 0 ? "info" : "muted"}
          />
          <span className="text-[var(--muted-foreground)]">
            平均 {summary?.average_score ?? 0} 分
          </span>
          <Link className="text-[var(--primary)] underline" href="/knowledge">
            合同正文也可以进知识库
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

      <ContractPanel contracts={contracts} />

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">分数口径</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            不按条数累加，按严重度加权——5 条「低」和 1 条「高」不是一回事
          </p>
          <table className="mt-3 w-full text-[11px]">
            <thead>
              <tr className="text-left text-[var(--muted-foreground)]">
                <th className="pb-1 font-medium">档位</th>
                <th className="pb-1 font-medium">权重</th>
                <th className="pb-1 font-medium">说明</th>
              </tr>
            </thead>
            <tbody className="text-[var(--foreground)]">
              {scoring
                .filter((row) => row.kind === "weight")
                .map((row) => (
                  <tr key={row.severity} className="border-t border-[var(--border)]">
                    <td className="py-1.5">{SEVERITY_LABEL_OF(row.severity)}</td>
                    <td className="py-1.5">{row.value}</td>
                    <td className="py-1.5 text-[var(--muted-foreground)]">{row.note}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          <ul className="mt-2 flex flex-col gap-0.5">
            {scoring
              .filter((row) => row.kind !== "weight")
              .map((row) => (
                <li key={row.kind} className="text-[11px] text-[var(--muted-foreground)]">
                  {row.value} · {row.note}
                </li>
              ))}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            {levelRows.map((row) => (
              <span key={row.key} className="flex items-center gap-1 text-[11px]">
                <StatusBadge label={row.label} tone={row.tone} />
                <span className="text-[var(--muted-foreground)]">
                  {summary?.by_level?.[row.key] ?? 0} 份
                </span>
              </span>
            ))}
          </div>
        </div>

        <div className="card p-4">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">风险类型分布</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            哪类条款反复出问题，就该去改模板，而不是一份份合同地谈
          </p>
          {(summary?.by_risk_key ?? []).length === 0 ? (
            <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">还没有命中记录</p>
          ) : (
            <ul className="mt-3 flex flex-col gap-1.5">
              {(summary?.by_risk_key ?? []).map((row) => (
                <li key={row.rule_key} className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="text-[var(--foreground)]">
                    {RULE_TITLES[row.rule_key] ?? row.rule_key}
                  </span>
                  <span className="text-[var(--muted-foreground)]">{row.count} 次</span>
                </li>
              ))}
            </ul>
          )}
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

function SEVERITY_LABEL_OF(severity: string): string {
  if (severity === "high") return "高";
  if (severity === "medium") return "中";
  if (severity === "low") return "低";
  return "—";
}
