"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Check, Loader2, RotateCcw, Scissors, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { LiveClip, LiveSession } from "@/types";

const STATUS_STYLE: Record<string, string> = {
  suggested: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  approved: "bg-[var(--success-soft,var(--primary-soft))] text-[var(--success,var(--primary))]",
  rejected: "bg-[var(--danger-soft)] text-[var(--danger)]",
};

const STATUS_LABEL: Record<string, string> = {
  suggested: "待定",
  approved: "已通过",
  rejected: "已驳回",
};

function clock(seconds: number): string {
  const mm = Math.floor(seconds / 60);
  const ss = seconds % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

/**
 * 剪辑台。
 *
 * 每条候选都先给「第几秒到第几秒」和「为什么选它」，再给话术原文。
 * 顺序反了的话，剪辑师得先读一遍内容才能判断要不要，那是把挑选成本推给人。
 */
export function LivePanel({ sessions }: { sessions: LiveSession[] }) {
  const router = useRouter();
  const [selectedId, setSelectedId] = useState<number | null>(sessions[0]?.id ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "suggested" | "approved">("all");

  const selected = sessions.find((item) => item.id === selectedId) ?? sessions[0] ?? null;

  async function act(action: "decide" | "recut", clip?: LiveClip, status?: string) {
    if (!selected) return;
    setBusy(clip ? `clip-${clip.id}-${status}` : "recut");
    setError(null);
    const response = await fetch("/live/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        clip_id: clip?.id,
        session_id: selected.id,
        status,
      }),
    }).catch(() => null);
    const payload = (await response?.json().catch(() => null)) as { error?: string } | null;
    setBusy(null);
    if (!response?.ok) {
      setError(payload?.error ?? "操作失败");
      return;
    }
    router.refresh();
  }

  if (!selected) {
    return (
      <section className="card p-5">
        <h2 className="text-sm font-semibold text-[var(--foreground)]">还没有直播场次</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
          直播切片吃的是转写文本：每句都要带 start（秒）和 text。视频裁剪不在这个模块里做。
        </p>
      </section>
    );
  }

  const clips = selected.clips.filter((item) => filter === "all" || item.status === filter);

  return (
    <section className="grid gap-4 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <div className="card overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">直播场次</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">按上传时间倒序</p>
        </div>
        <ul className="max-h-[520px] divide-y divide-[var(--border)] overflow-auto">
          {sessions.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={`w-full px-4 py-2.5 text-left transition ${
                  item.id === selected.id ? "bg-[var(--primary-soft)]" : "hover:bg-[var(--muted)]"
                }`}
              >
                <p className="truncate text-xs font-medium text-[var(--foreground)]">{item.title}</p>
                <p className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">
                  {item.clip_count} 个切片 · {clock(item.duration_seconds)} · {item.status}
                </p>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-4">
        <div className="card p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-[var(--foreground)]">
                {selected.title}
              </h2>
              <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                {selected.product_name || "未标商品"} · 时长 {clock(selected.duration_seconds)} ·
                规则版本 {selected.rule_version}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={() => act("recut")} disabled={busy !== null}>
              {busy === "recut" ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="mr-1 h-3.5 w-3.5" />
              )}
              重挑
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(
              [
                ["all", `全部 ${selected.clips.length}`],
                ["suggested", `待定 ${selected.clips.filter((c) => c.status === "suggested").length}`],
                ["approved", `已通过 ${selected.clips.filter((c) => c.status === "approved").length}`],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setFilter(key)}
                className={`rounded-full px-2 py-0.5 text-[11px] transition ${
                  filter === key
                    ? "bg-[var(--primary)] text-white"
                    : "bg-[var(--muted)] text-[var(--muted-foreground)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <ul className="grid gap-3">
          {clips.length === 0 ? (
            <li className="card p-6 text-center text-xs text-[var(--muted-foreground)]">
              这个筛选下没有候选。
            </li>
          ) : null}
          {clips.map((clip) => (
            <li key={clip.id} className="card p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-[var(--primary-soft)] px-1.5 py-0.5 text-[10px] text-[var(--primary)]">
                      {clock(clip.start_seconds)}–{clock(clip.end_seconds)}
                    </span>
                    <span className="text-xs font-medium text-[var(--foreground)]">{clip.title}</span>
                    <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${STATUS_STYLE[clip.status]}`}>
                      {STATUS_LABEL[clip.status]}
                    </span>
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-[var(--muted-foreground)]">{clip.score} 分</span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => act("decide", clip, "approved")}
                    disabled={busy !== null}
                  >
                    {busy === `clip-${clip.id}-approved` ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Check className="h-3.5 w-3.5" />
                    )}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => act("decide", clip, "rejected")}
                    disabled={busy !== null}
                  >
                    {busy === `clip-${clip.id}-rejected` ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <X className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </div>
              </div>

              <ul className="mt-2 flex flex-wrap gap-1.5">
                {clip.reasons.map((reason) => (
                  <li
                    key={reason}
                    className="rounded-full bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]"
                  >
                    {reason}
                  </li>
                ))}
              </ul>

              <p className="mt-2 border-l-2 border-[var(--border)] pl-2 text-[11px] leading-relaxed text-[var(--foreground)]">
                {clip.transcript}
              </p>
            </li>
          ))}
        </ul>

        {error ? (
          <p className="rounded-md bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
            {error}
          </p>
        ) : null}

        <p className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
          <Scissors className="h-3.5 w-3.5" />
          产出是「哪几句、第几秒、为什么」，成片要拿这组时间码去剪辑工具里出。
        </p>
      </div>
    </section>
  );
}
