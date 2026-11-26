"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FileText, Loader2, ScanSearch, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Contract, ContractRisk } from "@/types";

const SEVERITY_STYLE: Record<string, string> = {
  high: "bg-[var(--danger-soft)] text-[var(--danger)]",
  medium: "bg-[var(--warning-soft)] text-[var(--warning)]",
  low: "bg-[var(--muted)] text-[var(--muted-foreground)]",
};

const SEVERITY_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const SOURCE_LABEL: Record<string, string> = {
  rule: "规则",
  llm: "语义复核",
};

const DECISION_LABEL: Record<string, string> = {
  approved: "通过",
  rejected: "驳回",
  changes_requested: "退回修改",
};

const DECISION_OPTIONS = ["changes_requested", "rejected", "approved"] as const;

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 合同审批台。
 *
 * 左边清单、右边逐条风险。风险条目一律先摆原句再摆解释：审批人先看证据，
 * 再看系统怎么说——顺序反了就会变成"系统说什么就信什么"。
 */
export function ContractPanel({ contracts }: { contracts: Contract[] }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [selectedId, setSelectedId] = useState<number | null>(contracts[0]?.id ?? null);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = contracts.find((item) => item.id === selectedId) ?? contracts[0] ?? null;

  function flash(message: string | null, kind: "ok" | "err" = "ok") {
    setNotice(kind === "ok" ? message : null);
    setError(kind === "err" ? message : null);
  }

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      flash("先选一个 .txt / .md / .docx / .pdf", "err");
      return;
    }
    setUploading(true);
    flash(null);
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/contracts/upload", { method: "POST", body: form }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as
      | { error?: string; id?: number; clause_count?: number; risk_count?: number; duplicate_of?: number | null }
      | null;
    setUploading(false);

    if (!response?.ok) {
      flash(payload?.error ?? "上传失败", "err");
      return;
    }
    if (fileRef.current) fileRef.current.value = "";
    const dup = payload?.duplicate_of ? `（与 #${payload.duplicate_of} 是同一份文件）` : "";
    flash(`已切出 ${payload?.clause_count ?? 0} 条条款、规则命中 ${payload?.risk_count ?? 0} 条风险${dup}`);
    if (payload?.id) setSelectedId(payload.id);
    router.refresh();
  }

  async function act(action: "review" | "approve", decision?: string) {
    if (!selected) return;
    setBusy(action + (decision ?? ""));
    flash(null);
    const response = await fetch("/contracts/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, id: selected.id, decision, comment }),
    }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as
      | { error?: string; added?: number; dropped?: number; dropped_reason?: string }
      | null;
    setBusy(null);

    if (!response?.ok) {
      flash(payload?.error ?? "操作失败", "err");
      return;
    }
    if (action === "review") {
      const dropped = payload?.dropped
        ? `，丢弃 ${payload.dropped} 条（${payload.dropped_reason}）`
        : "";
      flash(`语义复核补充 ${payload?.added ?? 0} 条${dropped}`);
    } else {
      flash(`已记录：${DECISION_LABEL[decision ?? ""] ?? decision}`);
      setComment("");
    }
    router.refresh();
  }

  if (contracts.length === 0) {
    return (
      <section className="card p-5">
        <h2 className="text-sm font-semibold text-[var(--foreground)]">还没有合同</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
          支持 .txt / .md / .docx / .pdf。扫描件抽不出文字，会直接报错——不拿乱码去打分。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.docx,.pdf"
            className="text-xs text-[var(--muted-foreground)] file:mr-3 file:rounded-md file:border-0 file:bg-[var(--muted)] file:px-3 file:py-1.5 file:text-xs file:text-[var(--foreground)]"
          />
          <Button onClick={upload} disabled={uploading} size="sm">
            {uploading ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Upload className="mr-1 h-3.5 w-3.5" />}
            上传
          </Button>
        </div>
        {error ? <p className="mt-2 text-xs text-[var(--danger)]">{error}</p> : null}
      </section>
    );
  }

  return (
    <section className="grid gap-4 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
      <div className="card overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">合同清单</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            按上传时间倒序。分数口径见右下角
          </p>
          <div className="mt-2 flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.md,.docx,.pdf"
              className="min-w-0 flex-1 text-[11px] text-[var(--muted-foreground)] file:mr-2 file:rounded-md file:border-0 file:bg-[var(--muted)] file:px-2 file:py-1 file:text-[11px] file:text-[var(--foreground)]"
            />
            <Button onClick={upload} disabled={uploading} size="sm" variant="outline">
              {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            </Button>
          </div>
        </div>
        <ul className="max-h-[520px] divide-y divide-[var(--border)] overflow-auto">
          {contracts.map((item) => {
            const active = item.id === selected?.id;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedId(item.id);
                    flash(null);
                  }}
                  className={`w-full px-4 py-2.5 text-left transition ${active ? "bg-[var(--primary-soft)]" : "hover:bg-[var(--muted)]"}`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]" />
                      <span className="truncate text-xs font-medium text-[var(--foreground)]">
                        {item.filename}
                      </span>
                    </span>
                    <span
                      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${SEVERITY_STYLE[item.risk_level]}`}
                    >
                      {item.risk_score} 分
                    </span>
                  </span>
                  <span className="mt-1 flex items-center gap-2 text-[10px] text-[var(--muted-foreground)]">
                    <span>{item.clause_count} 条条款</span>
                    <span>·</span>
                    <span>{item.risk_count} 条风险</span>
                    {item.high_risk_count > 0 ? (
                      <span className="text-[var(--danger)]">高危 {item.high_risk_count}</span>
                    ) : null}
                    {item.semantic_checked ? <span>· 已语义复核</span> : <span>· 待复核</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="flex flex-col gap-4">
        {selected ? (
          <>
            <div className="card p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-base font-semibold text-[var(--foreground)]">
                    {selected.filename}
                  </h2>
                  <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                    {formatBytes(selected.size_bytes)} · {selected.content_type || "未知类型"} · 规则版本{" "}
                    {selected.rule_version}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] ${SEVERITY_STYLE[selected.risk_level]}`}>
                    {selected.risk_score} 分 · {SEVERITY_LABEL[selected.risk_level]}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => act("review")}
                    disabled={busy !== null}
                  >
                    {busy === "review" ? (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <ScanSearch className="mr-1 h-3.5 w-3.5" />
                    )}
                    语义复核
                  </Button>
                </div>
              </div>
              <p className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted-foreground)]">
                <span>规则 {selected.rule_score}</span>
                <span>+</span>
                <span>语义 {selected.semantic_score}</span>
                <span className="text-[10px]">
                  （两段分开看：规则可复现，语义要人来确认）
                </span>
              </p>
              {selected.summary ? (
                <p className="mt-3 rounded-md bg-[var(--muted)] px-3 py-2 text-[11px] leading-relaxed text-[var(--foreground)]">
                  语义复核结论：{selected.summary}
                </p>
              ) : (
                <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">
                  还没做语义复核。规则只能数清账期和比例，读不出「以甲方确认为准」这类把判断权
                  交给一方的表述。
                </p>
              )}
            </div>

            <div className="card overflow-hidden">
              <div className="border-b border-[var(--border)] px-4 py-3">
                <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--foreground)]">
                  {selected.high_risk_count > 0 ? (
                    <AlertTriangle className="h-4 w-4 text-[var(--danger)]" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4 text-[var(--success,var(--primary))]" />
                  )}
                  风险清单
                </h3>
                <p className="text-[11px] text-[var(--muted-foreground)]">
                  每条都先给原句再给解释——顺序反了就会变成「系统说什么就信什么」
                </p>
              </div>
              <ul className="max-h-[560px] divide-y divide-[var(--border)] overflow-auto">
                {selected.risks.length === 0 ? (
                  <li className="px-4 py-6 text-center text-xs text-[var(--muted-foreground)]">
                    规则没有命中任何风险。这不等于合同没问题——语义复核按下之后才能说这句。
                  </li>
                ) : null}
                {selected.risks.map((risk) => (
                  <RiskRow key={risk.id} risk={risk} />
                ))}
              </ul>
            </div>

            <div className="card p-4">
              <h3 className="text-sm font-semibold text-[var(--foreground)]">审批</h3>
              <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                系统只出风险清单，签不签由人定。高风险合同点「通过」必须写理由——不是拦住你，
                是让签字有据可查。
              </p>
              <textarea
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                rows={2}
                placeholder="审批意见（高风险通过时必填）"
                className="mt-2 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
              />
              <div className="mt-2 flex flex-wrap gap-2">
                {DECISION_OPTIONS.map((decision) => (
                  <Button
                    key={decision}
                    size="sm"
                    variant={decision === "approved" ? "default" : "outline"}
                    onClick={() => act("approve", decision)}
                    disabled={busy !== null}
                  >
                    {busy === `approve${decision}` ? (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    {DECISION_LABEL[decision]}
                  </Button>
                ))}
              </div>

              {selected.approvals.length > 0 ? (
                <ul className="mt-3 divide-y divide-[var(--border)] border-t border-[var(--border)] pt-2">
                  {selected.approvals.map((item) => (
                    <li key={item.id} className="py-2 text-[11px]">
                      <p className="flex items-center gap-2">
                        <span className="font-medium text-[var(--foreground)]">
                          {DECISION_LABEL[item.decision] ?? item.decision}
                        </span>
                        <span className="text-[var(--muted-foreground)]">{item.decided_at}</span>
                        <span className="text-[var(--muted-foreground)]">
                          快照 {item.risk_snapshot.length} 条
                        </span>
                      </p>
                      {item.comment ? (
                        <p className="mt-0.5 leading-relaxed text-[var(--muted-foreground)]">
                          {item.comment}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </>
        ) : null}

        {notice ? (
          <p className="rounded-md bg-[var(--primary-soft)] px-3 py-2 text-xs text-[var(--primary)]">
            {notice}
          </p>
        ) : null}
        {error ? (
          <p className="rounded-md bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
            {error}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function RiskRow({ risk }: { risk: ContractRisk }) {
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${SEVERITY_STYLE[risk.severity]}`}>
          {SEVERITY_LABEL[risk.severity]}
        </span>
        <span className="text-xs font-medium text-[var(--foreground)]">{risk.title}</span>
        <span className="rounded-full bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
          {SOURCE_LABEL[risk.source] ?? risk.source}
        </span>
        {risk.clause_ordinal ? (
          <span className="text-[10px] text-[var(--muted-foreground)]">
            第 {risk.clause_ordinal} 条 · {risk.clause_kind_title}
          </span>
        ) : (
          <span className="text-[10px] text-[var(--muted-foreground)]">全文范围</span>
        )}
      </div>
      <blockquote className="mt-2 border-l-2 border-[var(--border)] pl-2 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
        {risk.quote}
      </blockquote>
      <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--foreground)]">{risk.detail}</p>
      <p className="mt-1 text-[11px] leading-relaxed text-[var(--primary)]">建议：{risk.suggestion}</p>
    </li>
  );
}
