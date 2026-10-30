import { AlertTriangle, Bot, Database, PackageSearch, TrendingUp, Wallet } from "lucide-react";

import { DataPlatformPanel } from "@/components/data-platform-panel";
import { StatusBadge } from "@/components/ui";
import { Card } from "@/components/ui/card";
import {
  getDataPlatformLineage,
  getDataPlatformMetricSeries,
  getDataPlatformMetrics,
  getDataPlatformOverview,
  getDataPlatformQuality,
} from "@/lib/api";

const money = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 2 });

type Stat = {
  label: string;
  value: string;
  hint: string;
  icon: typeof Wallet;
  tone: string;
};

/** 指标序列的极简折线：不引图表库，够看出趋势即可。 */
function Sparkline({ points }: { points: { period: string | null; value: number | null }[] }) {
  const usable = points.filter(
    (point): point is { period: string; value: number } => point.value !== null,
  );
  if (usable.length < 2) {
    return <p className="text-[11px] text-[var(--muted-foreground)]">数据点不足，暂不成图</p>;
  }
  const values = usable.map((point) => point.value);
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const width = 280;
  const height = 48;
  const step = width / (usable.length - 1);
  const path = usable
    .map((point, index) => {
      const x = (index * step).toFixed(1);
      const y = (height - ((point.value - min) / span) * height).toFixed(1);
      return `${index === 0 ? "M" : "L"}${x},${y}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-12 w-full" role="img" aria-label="成交额趋势">
      <path d={path} fill="none" stroke="var(--primary)" strokeWidth="1.5" strokeLinejoin="round" />
      <text x="0" y="8" fontSize="9" fill="var(--muted-foreground)">
        {max.toFixed(1)}
      </text>
      <text x="0" y={height} fontSize="9" fill="var(--muted-foreground)">
        {min.toFixed(1)}
      </text>
    </svg>
  );
}

/** 指标卡：不依赖公共 StatCard，图标按值传入并在此处渲染，避免跨组件边界传组件引用。 */
function StatBlock({ stat }: { stat: Stat }) {
  const Icon = stat.icon;
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">{stat.label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-[var(--foreground)]">{stat.value}</p>
          <p className="mt-1 truncate text-[11px] text-[var(--muted-foreground)]">{stat.hint}</p>
        </div>
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] text-white shadow-[var(--shadow-btn)] ${stat.tone}`}
        >
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      </div>
    </Card>
  );
}

/** 数据中台页面：首屏全部由服务端取数，交互只有跑批与查询。 */
export default async function DataPlatformPage() {
  const [overview, metrics, quality, lineage, gmvSeries] = await Promise.all([
    getDataPlatformOverview(),
    getDataPlatformMetrics(),
    getDataPlatformQuality(),
    getDataPlatformLineage(),
    getDataPlatformMetricSeries("gmv", 14),
  ]);

  if (!overview) {
    return (
      <main className="flex flex-col gap-6">
        <Card className="p-5 text-sm text-[var(--muted-foreground)]">
          数据中台接口未就绪：请确认后端已启动、当前账号已登录。
        </Card>
      </main>
    );
  }

  const summary = overview.summary;
  const windowLabel = summary?.window_end
    ? `${summary.window_start ?? "—"} ~ ${summary.window_end}`
    : "暂无经营数据";
  const skuRows =
    overview.layers
      .find((layer) => layer.layer === "DWD")
      ?.tables.find((table) => table.name === "dwd_sku_dim")?.row_count ?? 0;

  const stats: Stat[] = [
    {
      label: "近 7 天成交额",
      value: summary ? `¥${money.format(summary.gmv_7d)}` : "—",
      hint: summary ? `消耗 ¥${money.format(summary.spend_7d)}` : "跑批后可见",
      icon: Wallet,
      tone: "bg-gradient-primary",
    },
    {
      label: "ROAS",
      value: summary?.roas_7d != null ? `${money.format(summary.roas_7d)}×` : "—",
      hint: summary?.cvr_7d != null ? `转化率 ${percent.format(summary.cvr_7d)}` : "先聚合再相除",
      icon: TrendingUp,
      tone: "bg-gradient-info",
    },
    {
      label: "低库存 SKU 占比",
      value: summary?.low_stock_ratio != null ? percent.format(summary.low_stock_ratio) : "—",
      hint: `${skuRows} 个 SKU 在册`,
      icon: PackageSearch,
      tone: "bg-gradient-warning",
    },
    {
      label: "Agent 任务成功率",
      value: summary?.agent_success_rate != null ? percent.format(summary.agent_success_rate) : "—",
      hint: summary ? `素材 ${summary.asset_count} 条 · 商品 ${summary.product_count} 个` : "无任务时为空",
      icon: Bot,
      tone: "bg-gradient-success",
    },
  ];

  const adsNodes = (lineage?.nodes ?? []).filter((node) => node.layer === "ADS");
  const checks = quality?.checks ?? [];

  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <Database className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--primary)]">Data Platform</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">数据中台</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">
              业务库只负责下单与录入，分析查询落在独立的 DuckDB 仓：ODS 原样落地、DWD 清洗对齐、
              DWS 主题汇总、ADS 直接出指标。同一个数只有一个口径，口径写在代码里并随每次跑批快照留档。
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge
            label={overview.ready ? "仓已就绪" : "尚未抽取"}
            tone={overview.ready ? "success" : "warning"}
          />
          <span className="text-[var(--muted-foreground)]">窗口 {windowLabel}</span>
          {overview.run ? (
            <span className="text-[var(--muted-foreground)]">
              · 上次跑批 {new Date(overview.run.finished_at).toLocaleString("zh-CN")} ·{" "}
              {overview.run.duration_ms} ms · {overview.run.ods_rows} 行
            </span>
          ) : null}
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <StatBlock key={stat.label} stat={stat} />
        ))}
      </div>

      <DataPlatformPanel ready={overview.ready} />

      <section className="grid gap-4 xl:grid-cols-4">
        {overview.layers.map((layer) => (
          <div key={layer.layer} className="card p-4">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-semibold text-[var(--foreground)]">{layer.layer}</span>
              <span className="text-[11px] text-[var(--muted-foreground)]">
                {layer.tables.reduce((total, table) => total + table.row_count, 0)} 行
              </span>
            </div>
            <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">{layer.label}</p>
            <div className="mt-2 space-y-1.5">
              {layer.tables.map((table) => (
                <div key={table.name} className="rounded-[8px] border border-[var(--border)] px-2 py-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-[var(--foreground)]">{table.name}</span>
                    <span className="shrink-0 text-[11px] tabular-nums text-[var(--muted-foreground)]">
                      {table.row_count}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-[10px] text-[var(--muted-foreground)]">{table.description}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card className="p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-[var(--foreground)]">指标口径</h2>
            <span className="text-[11px] text-[var(--muted-foreground)]">
              {metrics.length} 个 · 代码声明，跑批快照
            </span>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-[var(--muted-foreground)]">
                  <th className="py-1.5 pr-3 font-medium">指标</th>
                  <th className="py-1.5 pr-3 font-medium">单位</th>
                  <th className="py-1.5 pr-3 font-medium">取数表</th>
                  <th className="py-1.5 font-medium">公式</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((metric) => (
                  <tr key={metric.key} className="border-t border-[var(--border)] align-top">
                    <td className="py-2 pr-3">
                      <span className="font-medium text-[var(--foreground)]">{metric.name}</span>
                      <p className="mt-0.5 max-w-[22rem] text-[10px] leading-relaxed text-[var(--muted-foreground)]">
                        {metric.definition}
                      </p>
                    </td>
                    <td className="py-2 pr-3 text-[var(--muted-foreground)]">{metric.unit}</td>
                    <td className="py-2 pr-3">
                      <code className="text-[10px] text-[var(--muted-foreground)]">{metric.source_table}</code>
                    </td>
                    <td className="py-2">
                      <code className="text-[10px] text-[var(--foreground)]">{metric.formula}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="flex flex-col gap-6">
          <Card className="p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-[var(--foreground)]">成交额趋势</h2>
              <span className="text-[11px] text-[var(--muted-foreground)]">
                {gmvSeries?.has_data ? `${gmvSeries.points.length} 天` : "无数据"}
              </span>
            </div>
            <div className="mt-3">{gmvSeries ? <Sparkline points={gmvSeries.points} /> : null}</div>
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
              窗口右端是仓内最新业务日期，不是今天：补录上周数据时，看板不会显示成“近 7 天零消耗”。
            </p>
          </Card>

          <Card className="p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-[var(--foreground)]">质量校验</h2>
              <span className="text-[11px] text-[var(--muted-foreground)]">
                {quality ? `${quality.passed} 通过 / ${quality.failed} 未通过` : "未跑批"}
              </span>
            </div>
            <ul className="mt-3 space-y-2">
              {checks.map((check) => (
                <li key={check.key} className="flex items-start gap-2 text-xs">
                  {check.passed ? (
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--success)]" />
                  ) : (
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--danger)]" aria-hidden="true" />
                  )}
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[var(--foreground)]">{check.name}</span>
                      {check.severity === "warning" ? (
                        <span className="text-[10px] text-[var(--muted-foreground)]">提醒</span>
                      ) : null}
                      {check.passed ? null : (
                        <span className="text-[10px] text-[var(--danger)]">{check.observed} 条违规</span>
                      )}
                    </div>
                    <p className="mt-0.5 text-[10px] leading-relaxed text-[var(--muted-foreground)]">
                      {check.expectation}
                    </p>
                  </div>
                </li>
              ))}
              {checks.length === 0 ? (
                <li className="text-xs text-[var(--muted-foreground)]">跑一次批，这里会出现 9 条校验结果。</li>
              ) : null}
            </ul>
          </Card>
        </div>
      </section>

      <Card className="p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-[var(--foreground)]">表级血缘</h2>
          <span className="text-[11px] text-[var(--muted-foreground)]">
            {lineage ? `${lineage.nodes.length} 个节点 · ${lineage.edges.length} 条边` : "不可用"}
          </span>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
          边来自建表声明里的 depends_on，不靠解析 SQL：解析器读不懂 CREATE ... WITH 里到底用了哪几张表。
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {adsNodes.map((node) => (
            <div key={node.name} className="rounded-[8px] border border-[var(--border)] p-2.5">
              <code className="text-[11px] text-[var(--foreground)]">{node.name}</code>
              <p className="mt-1 text-[10px] text-[var(--muted-foreground)]">上游</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {(lineage?.upstream[node.name] ?? []).map((source) => (
                  <span
                    key={source}
                    className="rounded-[6px] bg-[var(--secondary)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]"
                  >
                    {source}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </main>
  );
}
