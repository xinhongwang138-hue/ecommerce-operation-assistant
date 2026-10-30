"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Database, Loader2, Play, TerminalSquare } from "lucide-react";

import { Card } from "@/components/ui/card";
import type { DataPlatformQueryResult } from "@/types";

const SAMPLE_QUERIES = [
  { label: "各店 7 天 KPI", sql: "SELECT * FROM ads_store_kpi ORDER BY gmv_7d DESC" },
  {
    label: "库存风险商品",
    sql: "SELECT product_name, sku_count, low_stock_skus FROM ads_product_health WHERE risk_label = '库存风险'",
  },
  { label: "近 14 天日指标", sql: "SELECT date, gmv, spend, roas FROM ads_ops_daily ORDER BY date" },
];

/** 数据中台交互面板：跑批与只读查询。首屏数据由服务端渲染，这里只负责动作。 */
export function DataPlatformPanel({ ready }: { ready: boolean }) {
  const router = useRouter();
  const [running, setRunning] = useState(false);
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [sql, setSql] = useState(SAMPLE_QUERIES[0].sql);
  const [result, setResult] = useState<DataPlatformQueryResult | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  async function runPipeline() {
    setRunning(true);
    setRunMessage(null);
    try {
      const response = await fetch("/data-platform/run", { method: "POST" });
      const payload = (await response.json()) as
        | { run: { ods_rows: number; tables_built: number; checks_passed: number; checks_failed: number } }
        | { error?: string };
      if (!response.ok) {
        setRunMessage(("error" in payload && payload.error) || `抽取失败（${response.status}）`);
        return;
      }
      const run = (payload as { run: { ods_rows: number; tables_built: number; checks_passed: number; checks_failed: number } }).run;
      setRunMessage(
        `已抽取 ${run.ods_rows} 行，建表 ${run.tables_built} 张，质量校验 ${run.checks_passed} 通过 / ${run.checks_failed} 未通过`,
      );
      router.refresh();
    } catch {
      setRunMessage("抽取请求未送达，请确认后端已启动");
    } finally {
      setRunning(false);
    }
  }

  async function runQuery() {
    setQuerying(true);
    setQueryError(null);
    setResult(null);
    try {
      const response = await fetch("/data-platform/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      });
      const payload = (await response.json()) as DataPlatformQueryResult | { error?: string };
      if (!response.ok) {
        setQueryError(("error" in payload && payload.error) || `查询失败（${response.status}）`);
        return;
      }
      setResult(payload as DataPlatformQueryResult);
    } catch {
      setQueryError("查询请求未送达，请确认后端已启动");
    } finally {
      setQuerying(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
      <Card className="card p-5">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <Database className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-[var(--foreground)]">全量重算</h2>
            <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
              从业务库按 Workspace 抽取，依次重建 ODS / DWD / DWS / ADS 四层，然后跑质量校验。
              每次都是整层覆盖，不做增量——跑完的仓一定等于源库当前状态。
            </p>
          </div>
        </div>

        <button className="btn-primary mt-4 w-full" onClick={runPipeline} disabled={running} type="button">
          {running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
          {running ? "正在抽取…" : ready ? "重新抽取并重建" : "首次抽取"}
        </button>

        {runMessage ? (
          <p className="mt-3 rounded-[10px] border border-[var(--border)] bg-[var(--secondary)] px-3 py-2 text-xs leading-relaxed text-[var(--foreground)]">
            {runMessage}
          </p>
        ) : null}

        <p className="mt-3 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
          跑批是同步的：数据量在十万行内是秒级。超过这个量级再换队列，否则只会把
          &quot;点完不知道好没好&quot;变成新问题。
        </p>
      </Card>

      <Card className="card p-5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <TerminalSquare className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h2 className="text-base font-semibold text-[var(--foreground)]">只读查询</h2>
          </div>
          <span className="badge-gradient badge-muted text-[11px]">单条 SELECT · 表白名单 · 最多 500 行</span>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {SAMPLE_QUERIES.map((sample) => (
            <button
              key={sample.label}
              className="btn-ghost h-7 px-2 text-[11px]"
              onClick={() => setSql(sample.sql)}
              type="button"
            >
              {sample.label}
            </button>
          ))}
        </div>

        <textarea
          className="input mt-3 h-24 w-full resize-y font-mono text-xs leading-relaxed"
          value={sql}
          onChange={(event) => setSql(event.target.value)}
          spellCheck={false}
        />

        <button className="btn-primary mt-3 h-9 px-4 text-xs" onClick={runQuery} disabled={querying} type="button">
          {querying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          {querying ? "执行中…" : "执行查询"}
        </button>

        {!ready ? (
          <p className="mt-3 text-xs text-[var(--muted-foreground)]">仓还没建，先点左侧抽取。</p>
        ) : null}

        {queryError ? (
          <p className="mt-3 rounded-[10px] border border-[var(--danger)] bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
            {queryError}
          </p>
        ) : null}

        {result ? (
          <div className="mt-3">
            <p className="text-[11px] text-[var(--muted-foreground)]">
              {result.row_count} 行 · {result.elapsed_ms} ms
              {result.truncated ? ` · 已截断到 ${result.max_rows} 行` : ""}
            </p>
            <div className="mt-2 max-h-64 overflow-auto rounded-[10px] border border-[var(--border)]">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-[var(--secondary)]">
                  <tr>
                    {result.columns.map((column) => (
                      <th key={column} className="whitespace-nowrap px-2 py-1.5 font-medium text-[var(--foreground)]">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-t border-[var(--border)]">
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="whitespace-nowrap px-2 py-1.5 text-[var(--muted-foreground)]">
                          {cell === null ? "—" : String(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
