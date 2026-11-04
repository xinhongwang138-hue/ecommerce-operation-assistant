import Link from "next/link";
import { AlertTriangle, ClipboardCheck, Gauge, ListChecks, Users } from "lucide-react";

import { QaPanel } from "@/components/qa-panel";
import { StatusBadge } from "@/components/ui";
import { getQaLeaderboard, getQaSessions, getQaSummary } from "@/lib/api";

const DAYS = 30;

function gradeTone(grade: string) {
  if (grade === "A") return "success" as const;
  if (grade === "B") return "info" as const;
  if (grade === "C") return "warning" as const;
  return "danger" as const;
}

/**
 * 销售考核（P09）：导入会话 → 规则评分 → 排行榜 + 个人报告。
 *
 * 首屏把所有"能一眼判断好坏"的数字摆出来（均分、红线数、等级分布），
 * 逐条证据在点击会话后在客户端取——报告很长，不该拖慢首屏。
 */
export default async function QaPage() {
  const [summary, leaderboard, sessions] = await Promise.all([
    getQaSummary(DAYS),
    getQaLeaderboard(DAYS),
    getQaSessions(20, DAYS),
  ]);

  const cards = [
    {
      label: "周期会话",
      value: `${summary?.sessions ?? 0}`,
      hint: `近 ${DAYS} 天 · 已评分 ${summary?.scored ?? 0}`,
      icon: ClipboardCheck,
    },
    {
      label: "平均分",
      value: summary?.average != null ? summary.average.toFixed(1) : "—",
      hint: "百分制，五条规则加权",
      icon: Gauge,
    },
    {
      label: "红线会话",
      value: `${summary?.redline_sessions ?? 0}`,
      hint: "命中即总分归零",
      icon: AlertTriangle,
    },
    {
      label: "被考核坐席",
      value: `${summary?.agents ?? 0}`,
      hint: "排行榜按坐席聚合",
      icon: Users,
    },
  ];

  const distribution = summary?.grade_distribution ?? {};
  const rows = leaderboard?.rows ?? [];

  const principles = [
    {
      title: "扣分必须能指回原句",
      body: "每条规则都产出证据（第几句、原话、为什么扣），只给一个总分在考核场景里是不合格的——坐席有权知道哪句说错了。",
    },
    {
      title: "红线一票否决，不做扣分",
      body: "绝对化承诺、私下交易、失礼推诿命中即归零。做成「扣 30 分」会让它挤进 B 级，那不是能商量的事。",
    },
    {
      title: "不猜，缺数据就说明",
      body: "缺时间戳时首响时效判 0 并写明「算不上来」，不假设一个大概的延迟。编出来的分数比低分更糟。",
    },
  ];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <ClipboardCheck className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">AI Sales Assessment</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">销售考核</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              导入客服会话 → 规则引擎逐条打分 → 坐席排行榜与单会话报告。评分只看两条硬线：
              每条扣分都能指回原句，踩红线的会话直接归零。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            label={`规则版本 ${summary?.rule_version ?? "—"}`}
            tone={summary ? "info" : "muted"}
          />
          <span className="text-[var(--muted-foreground)]">
            {summary?.unscored ? `${summary.unscored} 条会话尚未评分` : "全部会话已评分"}
          </span>
          <Link className="text-[var(--primary)] underline" href="/agent">
            会话来自本仓 Agent 中心？先去那边跑一轮
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

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-[var(--foreground)]">坐席排行榜</h2>
              <p className="text-[11px] text-[var(--muted-foreground)]">
                近 {DAYS} 天 · 按平均分降序，同分看会话量（样本太少的高分不该排第一）
              </p>
            </div>
            <span className="text-[11px] text-[var(--muted-foreground)]">
              共 {leaderboard?.total_sessions ?? 0} 条会话
            </span>
          </div>

          {rows.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
              还没有可排名的会话。用下面的「拉取仓内会话」或导入一批客服会话后再看。
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-[var(--muted-foreground)]">
                    <th className="px-4 py-2 font-medium">#</th>
                    <th className="px-4 py-2 font-medium">坐席</th>
                    <th className="px-4 py-2 font-medium">平均分</th>
                    <th className="px-4 py-2 font-medium">会话</th>
                    <th className="px-4 py-2 font-medium">A 级</th>
                    <th className="px-4 py-2 font-medium">红线</th>
                    <th className="px-4 py-2 font-medium">最好 / 最差</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.agent_name} className="border-t border-[var(--border)]">
                      <td className="px-4 py-2 text-[var(--muted-foreground)]">{index + 1}</td>
                      <td className="px-4 py-2 font-medium text-[var(--foreground)]">{row.agent_name}</td>
                      <td className="px-4 py-2 font-semibold text-[var(--foreground)]">
                        {row.average.toFixed(1)}
                      </td>
                      <td className="px-4 py-2 text-[var(--muted-foreground)]">{row.sessions}</td>
                      <td className="px-4 py-2 text-[var(--muted-foreground)]">{row.a_count}</td>
                      <td className="px-4 py-2">
                        {row.redline_count > 0 ? (
                          <span className="text-[var(--danger)]">{row.redline_count}</span>
                        ) : (
                          <span className="text-[var(--muted-foreground)]">0</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-[var(--muted-foreground)]">
                        {row.best.toFixed(0)} / {row.worst.toFixed(0)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card p-4">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">等级分布</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">A ≥ 85 · B ≥ 70 · C ≥ 60 · D &lt; 60</p>
          <ul className="mt-3 flex flex-col gap-2">
            {(["A", "B", "C", "D"] as const).map((grade) => {
              const count = distribution[grade] ?? 0;
              const total = summary?.scored || 0;
              const ratio = total > 0 ? Math.round((count / total) * 100) : 0;
              return (
                <li key={grade} className="flex items-center gap-3">
                  <StatusBadge label={grade} tone={gradeTone(grade)} />
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--muted)]">
                    <div
                      className="h-full rounded-full bg-[var(--primary)]"
                      style={{ width: `${ratio}%` }}
                    />
                  </div>
                  <span className="w-16 text-right text-[11px] text-[var(--muted-foreground)]">
                    {count} 条 · {ratio}%
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-4 flex items-start gap-1.5 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
            <ListChecks className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            红线会话固定落在 D 且单独计数——它们不是"分数低"，是不该发生。
          </p>
        </div>
      </section>

      <QaPanel sessions={sessions?.items ?? []} />

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
