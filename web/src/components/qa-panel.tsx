"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { AlertTriangle, Download, Loader2, RefreshCw, ScanSearch, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { QaReport, QaSessionItem } from "@/types";

const scoreTone = (total: number | null) => {
  if (total == null) return "text-[var(--muted-foreground)]";
  if (total >= 85) return "text-[var(--success)]";
  if (total >= 60) return "text-[var(--foreground)]";
  return "text-[var(--danger)]";
};

/**
 * 会话列表 + 单会话报告。
 *
 * 报告按需拉取：逐项得分与证据加起来比列表本身大得多，塞进首屏只会拖慢打开速度。
 */
export function QaPanel({ sessions }: { sessions: QaSessionItem[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<QaReport | null>(null);
  const [loadingReport, setLoadingReport] = useState<number | null>(null);

  async function runAction(action: "import-agent" | "rescore") {
    setBusy(action);
    setMessage(null);
    setError(null);
    try {
      const response = await fetch("/qa/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const payload = (await response.json()) as Record<string, unknown> & { error?: string };
      if (!response.ok) {
        setError(payload.error ?? `操作失败（${response.status}）`);
        return;
      }
      if (action === "import-agent") {
        const imported = Number(payload.imported ?? 0);
        const reason = String(payload.reason ?? "");
        setMessage(
          imported > 0
            ? `已拉取 ${imported} 条会话并完成评分`
            : `没有可拉取的会话${reason ? `：${reason}` : ""}`,
        );
      } else {
        setMessage(`已按当前规则重算 ${Number(payload.rescored ?? 0)} 条会话`);
      }
      router.refresh();
    } catch {
      setError("请求未送达，请确认后端已启动");
    } finally {
      setBusy(null);
    }
  }

  async function openReport(sessionId: number) {
    setLoadingReport(sessionId);
    setError(null);
    try {
      const response = await fetch(`/qa/report/${sessionId}`);
      const payload = (await response.json()) as QaReport | { error?: string };
      if (!response.ok) {
        setError(("error" in payload && payload.error) || `读取报告失败（${response.status}）`);
        return;
      }
      setReport(payload as QaReport);
    } catch {
      setError("请求未送达，请确认后端已启动");
    } finally {
      setLoadingReport(null);
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-[var(--foreground)]">会话与个人报告</h2>
            <p className="text-[11px] text-[var(--muted-foreground)]">
              点任意一条会话看逐项得分与证据原句；报告里的每句话都能对回下面的原文。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-xs"
              onClick={() => runAction("import-agent")}
              disabled={busy !== null}
            >
              {busy === "import-agent" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <ScanSearch className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              拉取仓内会话
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-xs"
              onClick={() => runAction("rescore")}
              disabled={busy !== null}
            >
              {busy === "rescore" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              按当前规则重算
            </Button>
            <Button asChild variant="outline" size="sm" className="text-xs">
              <a href="/qa/export?threshold=60">
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                导出低分样本
              </a>
            </Button>
          </div>
        </div>

        {(message || error) && (
          <p
            className={`mt-3 rounded-[8px] px-3 py-2 text-[11px] ${
              error
                ? "bg-[var(--danger-soft)] text-[var(--danger)]"
                : "bg-[var(--primary-soft)] text-[var(--primary)]"
            }`}
          >
            {error ?? message}
          </p>
        )}
      </div>

      {sessions.length === 0 ? (
        <div className="card p-8 text-center text-sm text-[var(--muted-foreground)]">
          这个周期还没有会话。点「拉取仓内会话」从本仓 Agent 中心取，或调用
          <code className="mx-1 rounded bg-[var(--muted)] px-1">POST /api/v1/qa/import</code>
          批量导入客服会话。
        </div>
      ) : (
        <div className="card overflow-hidden">
          <ul className="divide-y divide-[var(--border)]">
            {sessions.map((session) => (
              <li key={session.id}>
                <button
                  type="button"
                  onClick={() => openReport(session.id)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)]/60"
                >
                  <span className={`w-14 shrink-0 text-lg font-semibold ${scoreTone(session.total)}`}>
                    {session.total != null ? session.total.toFixed(0) : "—"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-[var(--foreground)]">
                        {session.external_id}
                      </span>
                      <span className="text-[11px] text-[var(--muted-foreground)]">
                        {session.agent_name} · {session.channel} · {session.message_count} 句
                      </span>
                      {session.redline && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--danger-soft)] px-1.5 py-0.5 text-[10px] text-[var(--danger)]">
                          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                          红线
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-[var(--muted-foreground)]">
                      {new Date(session.started_at).toLocaleString("zh-CN")} · 等级{" "}
                      {session.grade ?? "—"}
                    </span>
                  </span>
                  {loadingReport === session.id ? (
                    <Loader2 className="h-4 w-4 animate-spin text-[var(--muted-foreground)]" aria-hidden="true" />
                  ) : (
                    <span className="text-[11px] text-[var(--primary)]">看报告</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report && (
        <div className="card p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] text-[var(--muted-foreground)]">
                {report.session.external_id} · {report.session.agent_name} ·{" "}
                {report.session.channel}
              </p>
              <h3 className="mt-1 flex items-baseline gap-2 text-lg font-semibold text-[var(--foreground)]">
                <span className={scoreTone(report.score.total)}>{report.score.total.toFixed(1)}</span>
                <span className="text-xs font-normal text-[var(--muted-foreground)]">
                  等级 {report.score.grade} · 规则 {report.score.rule_version}
                </span>
              </h3>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-xs"
              onClick={() => setReport(null)}
              aria-label="关闭报告"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>

          {report.redline_evidence.length > 0 && (
            <div className="mt-3 rounded-[8px] border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-3">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-[var(--danger)]">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                红线命中（总分归零）
              </p>
              <ul className="mt-2 flex flex-col gap-1">
                {report.redline_evidence.map((item) => (
                  <li key={`${item.key}-${item.seq}`} className="text-[11px] text-[var(--danger)]">
                    <span className="font-medium">{item.title}</span>：第 {item.seq + 1} 句「
                    {item.excerpt}」命中「{item.phrase}」
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-3 flex flex-col gap-2">
            {report.score.breakdown.map((rule) => (
              <div key={rule.key} className="rounded-[8px] border border-[var(--border)] p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-[var(--foreground)]">
                    {rule.title}
                    <span className="ml-1 text-[10px] font-normal text-[var(--muted-foreground)]">
                      {rule.dimension}
                    </span>
                  </p>
                  <p
                    className={`text-xs font-semibold ${
                      rule.passed ? "text-[var(--success)]" : "text-[var(--danger)]"
                    }`}
                  >
                    {rule.score} / {rule.max_score}
                  </p>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                  {rule.detail}
                </p>
                {rule.evidence.length > 0 && (
                  <ul className="mt-2 flex flex-col gap-1">
                    {rule.evidence.map((evidence, index) => (
                      <li
                        key={`${rule.key}-${evidence.seq}-${index}`}
                        className="rounded bg-[var(--muted)]/60 px-2 py-1 text-[11px] text-[var(--foreground)]"
                      >
                        <span className="text-[var(--muted-foreground)]">
                          第 {evidence.seq + 1} 句（{evidence.role === "customer" ? "客户" : "坐席"}）：
                        </span>
                        {evidence.excerpt}
                        <span className="mt-0.5 block text-[10px] text-[var(--muted-foreground)]">
                          {evidence.note}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>

          {report.suggestions.length > 0 && (
            <div className="mt-3 rounded-[8px] bg-[var(--primary-soft)] p-3">
              <p className="text-xs font-semibold text-[var(--primary)]">接下来改什么</p>
              <ol className="mt-1 flex list-decimal flex-col gap-1 pl-4">
                {report.suggestions.map((item) => (
                  <li key={item} className="text-[11px] leading-relaxed text-[var(--primary)]">
                    {item}
                  </li>
                ))}
              </ol>
            </div>
          )}

          <div className="mt-3">
            <p className="text-xs font-semibold text-[var(--foreground)]">会话原文</p>
            <ul className="mt-2 flex flex-col gap-1.5">
              {report.messages.map((message) => (
                <li
                  key={message.seq}
                  className={`max-w-[85%] rounded-[8px] px-2.5 py-1.5 text-[11px] leading-relaxed ${
                    message.role === "customer"
                      ? "bg-[var(--muted)] text-[var(--foreground)]"
                      : "ml-auto bg-[var(--primary-soft)] text-[var(--foreground)]"
                  }`}
                >
                  <span className="mr-1 text-[10px] text-[var(--muted-foreground)]">
                    {message.role === "customer" ? "客户" : "坐席"}·{message.seq + 1}
                  </span>
                  {message.content}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
