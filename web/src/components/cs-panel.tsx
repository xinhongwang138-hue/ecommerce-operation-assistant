"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, MessageSquare, RotateCcw, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CsChatResult, CsTicket, CsTrack } from "@/types";

const TRACK_STYLE: Record<CsTrack, { label: string; className: string }> = {
  faq: { label: "FAQ 直答", className: "bg-[var(--primary-soft)] text-[var(--primary)]" },
  retrieval: { label: "资料检索", className: "bg-[var(--muted)] text-[var(--foreground)]" },
  handoff: { label: "转人工", className: "bg-[var(--danger-soft)] text-[var(--danger)]" },
  refuse: { label: "拒答", className: "bg-[var(--warning-soft)] text-[var(--warning)]" },
};

const SAMPLES = [
  "现货多久发货？",
  "12 包装的库存还剩多少，需要补货吗",
  "你们公司什么时候上市",
  "我要投诉，你们发的是假货",
];

type Bubble = {
  role: "user" | "assistant";
  content: string;
  track?: CsTrack;
  reason?: string;
  citations?: CsChatResult["citations"];
  handoff?: CsChatResult["handoff"];
};

/**
 * 客服对话台。
 *
 * 每条回复都带轨道标记与理由：用户看到的不只是答案，还有"这个答案凭什么"。
 * 拒答也是正常结果，不该被藏起来——它是最能说明系统边界的那个状态。
 */
export function CsPanel({ tickets }: { tickets: CsTicket[] }) {
  const router = useRouter();
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [closing, setClosing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(text: string) {
    const message = text.trim();
    if (!message) {
      return;
    }
    setSending(true);
    setError(null);
    setBubbles((current) => [...current, { role: "user", content: message }]);
    setInput("");
    try {
      const response = await fetch("/cs/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, conversation_id: conversationId }),
      });
      const payload = (await response.json()) as CsChatResult | { error?: string };
      if (!response.ok) {
        setError(("error" in payload && payload.error) || `发送失败（${response.status}）`);
        return;
      }
      const result = payload as CsChatResult;
      setConversationId(result.conversation_id);
      setBubbles((current) => [
        ...current,
        {
          role: "assistant",
          content: result.reply,
          track: result.track,
          reason: result.reason,
          citations: result.citations,
          handoff: result.handoff,
        },
      ]);
      if (result.handoff) {
        // 建了工单，右侧列表要跟着变
        router.refresh();
      }
    } catch {
      setError("请求未送达，请确认后端已启动");
    } finally {
      setSending(false);
    }
  }

  async function closeTicket(id: number) {
    setClosing(id);
    setError(null);
    try {
      const response = await fetch(`/cs/tickets/${id}/close`, { method: "POST" });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setError(payload?.error ?? `关闭失败（${response.status}）`);
        return;
      }
      router.refresh();
    } catch {
      setError("请求未送达，请确认后端已启动");
    } finally {
      setClosing(null);
    }
  }

  function reset() {
    setBubbles([]);
    setConversationId(null);
    setError(null);
  }

  return (
    <section className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <div className="card flex flex-col">
        <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-[var(--foreground)]">对话</h2>
            <p className="text-[11px] text-[var(--muted-foreground)]">
              {conversationId ? `会话 #${conversationId}（后续消息续在同一会话里）` : "新会话"}
            </p>
          </div>
          <Button variant="ghost" size="sm" className="text-xs" onClick={reset} type="button">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            新会话
          </Button>
        </div>

        <div className="flex min-h-[280px] flex-col gap-3 px-4 py-3">
          {bubbles.length === 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-[var(--muted-foreground)]">试一句：</p>
              <div className="flex flex-wrap gap-2">
                {SAMPLES.map((sample) => (
                  <button
                    key={sample}
                    type="button"
                    onClick={() => send(sample)}
                    className="rounded-full border border-[var(--border)] px-3 py-1 text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
                  >
                    {sample}
                  </button>
                ))}
              </div>
            </div>
          )}

          {bubbles.map((bubble, index) => (
            <div
              key={`${bubble.role}-${index}`}
              className={`max-w-[92%] ${bubble.role === "user" ? "ml-auto" : ""}`}
            >
              <div
                className={`whitespace-pre-wrap rounded-[10px] px-3 py-2 text-xs leading-relaxed ${
                  bubble.role === "user"
                    ? "bg-[var(--primary)] text-white"
                    : "bg-[var(--muted)] text-[var(--foreground)]"
                }`}
              >
                {bubble.content}
              </div>
              {bubble.role === "assistant" && bubble.track && (
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[10px] ${TRACK_STYLE[bubble.track].className}`}
                  >
                    {TRACK_STYLE[bubble.track].label}
                  </span>
                  <span className="text-[10px] text-[var(--muted-foreground)]">{bubble.reason}</span>
                </div>
              )}
              {bubble.citations && bubble.citations.length > 0 && (
                <ul className="mt-1 flex flex-col gap-1">
                  {bubble.citations.map((citation) => (
                    <li
                      key={citation.label}
                      className="rounded bg-[var(--background)] px-2 py-1 text-[10px] text-[var(--muted-foreground)]"
                    >
                      依据 · {citation.label}
                      {citation.score != null ? `（相似度 ${citation.score.toFixed(2)}）` : ""}
                    </li>
                  ))}
                </ul>
              )}
              {bubble.handoff && (
                <p className="mt-1 flex items-center gap-1 text-[10px] text-[var(--danger)]">
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                  已建工单 #{bubble.handoff.id}（{bubble.handoff.trigger} · {bubble.handoff.priority}）
                </p>
              )}
            </div>
          ))}

          {sending && (
            <p className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              正在判断该走哪条路…
            </p>
          )}
        </div>

        {error && (
          <p className="mx-4 mb-2 rounded-[8px] bg-[var(--danger-soft)] px-3 py-2 text-[11px] text-[var(--danger)]">
            {error}
          </p>
        )}

        <div className="mt-auto flex items-center gap-2 border-t border-[var(--border)] px-4 py-3">
          <input
            className="h-9 flex-1 rounded-[8px] border border-[var(--border)] bg-[var(--background)] px-3 text-xs text-[var(--foreground)] outline-none focus:border-[var(--primary)]"
            placeholder="描述问题，例如：12 包装还剩多少库存"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(input);
              }
            }}
            disabled={sending}
          />
          <Button size="sm" onClick={() => void send(input)} disabled={sending || !input.trim()}>
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            发送
          </Button>
        </div>
      </div>

      <div className="card flex flex-col">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">转人工工单</h2>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            同一会话只建一张，避免人工对着几张一样的单子逐一确认
          </p>
        </div>

        {tickets.length === 0 ? (
          <div className="px-4 py-8 text-center text-xs text-[var(--muted-foreground)]">
            还没有工单。说一句「我要投诉」就能看到转人工链路。
          </div>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {tickets.map((ticket) => (
              <li key={ticket.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-[var(--foreground)]">
                      #{ticket.id}
                      <span className="rounded-full bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
                        {ticket.trigger}
                      </span>
                      <span
                        className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                          ticket.priority === "high"
                            ? "bg-[var(--danger-soft)] text-[var(--danger)]"
                            : "bg-[var(--muted)] text-[var(--muted-foreground)]"
                        }`}
                      >
                        {ticket.priority}
                      </span>
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                      {ticket.reason}
                    </p>
                    <p className="mt-1 text-[10px] text-[var(--muted-foreground)]">
                      会话 #{ticket.conversation_id} ·{" "}
                      {new Date(ticket.created_at).toLocaleString("zh-CN")}
                    </p>
                  </div>
                  {ticket.status === "closed" ? (
                    <span className="flex shrink-0 items-center gap-1 text-[10px] text-[var(--success)]">
                      <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                      已关闭
                    </span>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0 text-[11px]"
                      onClick={() => void closeTicket(ticket.id)}
                      disabled={closing === ticket.id}
                      type="button"
                    >
                      {closing === ticket.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      ) : (
                        <MessageSquare className="h-3 w-3" aria-hidden="true" />
                      )}
                      关闭
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
