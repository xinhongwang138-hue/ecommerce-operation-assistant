"use client";

import { useState } from "react";
import { CornerDownLeft, Loader2, Sparkles, Table2 } from "lucide-react";

import type { Nl2SqlAnswer, Nl2SqlExample } from "@/types";

type ErrorState = { message: string; attempts: string[] };

/**
 * 问数面板：一个输入框，一个结果区。
 *
 * 结果区刻意先给 SQL 再给表格——问数的信任问题不在"数对不对"，在"我能不能看懂
 * 它是怎么算的"。把 SQL 摆在表格上面，用户第一眼看到的是口径而不是结论。
 */
export function Nl2SqlPanel({ examples }: { examples: Nl2SqlExample[] }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Nl2SqlAnswer | null>(null);
  const [error, setError] = useState<ErrorState | null>(null);
  const [loading, setLoading] = useState(false);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed) {
      setError({ message: "先写一个问题，例如「近 14 天成交额走势」", attempts: [] });
      return;
    }
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      const response = await fetch("/ask/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      const payload = (await response.json()) as
        | Nl2SqlAnswer
        | { error?: string; attempts?: string[] };
      if (!response.ok) {
        // 成功体里的 attempts 是数字（第几次尝试），失败体里是字符串数组（每轮的原因）。
        // 两个字段同名不同型，所以只在这个分支里按失败体取。
        const failure = payload as { error?: string; attempts?: string[] };
        setError({
          message: failure.error || `问数失败（${response.status}）`,
          attempts: failure.attempts ?? [],
        });
        return;
      }
      setAnswer(payload as Nl2SqlAnswer);
    } catch {
      setError({ message: "请求未送达，请确认后端已启动", attempts: [] });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card p-5">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
          <h2 className="text-base font-semibold text-[var(--foreground)]">直接用中文问</h2>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
          模型只负责写 SQL，执行前一律先过数据中台的只读护栏：单条 SELECT、表白名单、最多 500 行。
          模型写的 SQL 和你手敲的 SQL 走同一条边界。
        </p>

        <textarea
          className="input mt-3 h-20 w-full resize-y text-sm leading-relaxed"
          placeholder="例如：近 14 天每天的成交额和 ROAS 走势"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              ask(question);
            }
          }}
          spellCheck={false}
        />

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            className="btn-primary h-9 px-4 text-xs"
            onClick={() => ask(question)}
            disabled={loading}
            type="button"
          >
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <CornerDownLeft className="mr-2 h-4 w-4" />
            )}
            {loading ? "生成并执行中…" : "提问"}
          </button>
          <span className="text-[11px] text-[var(--muted-foreground)]">Ctrl / ⌘ + Enter</span>
        </div>

        <div className="mt-4">
          <p className="text-[11px] font-medium text-[var(--muted-foreground)]">试试这些</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {examples.map((example) => (
              <button
                key={example.question}
                className="group rounded-[10px] border border-[var(--border)] px-2.5 py-1.5 text-left transition hover:border-[var(--primary)] hover:bg-[var(--primary-soft)]"
                onClick={() => {
                  setQuestion(example.question);
                  ask(example.question);
                }}
                disabled={loading}
                type="button"
              >
                <span className="block text-[11px] text-[var(--foreground)]">{example.question}</span>
                <span className="mt-0.5 block font-mono text-[10px] text-[var(--muted-foreground)]">
                  {example.table} · {example.note}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? (
        <div className="rounded-[10px] border border-[var(--danger)] bg-[var(--danger-soft)] px-4 py-3">
          <p className="text-sm text-[var(--danger)]">{error.message}</p>
          {error.attempts.length > 0 ? (
            <ol className="mt-2 list-decimal space-y-1 pl-4 text-[11px] leading-relaxed text-[var(--danger)]">
              {error.attempts.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}

      {answer ? (
        <div className="card p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Table2 className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
              <h2 className="text-base font-semibold text-[var(--foreground)]">
                {answer.row_count} 行结果
              </h2>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted-foreground)]">
              <span className="badge-gradient badge-muted">{Math.round(answer.confidence * 100)}% 置信</span>
              <span>{answer.elapsed_ms} ms</span>
              <span>第 {answer.attempts} 次尝试</span>
              {answer.truncated ? <span className="text-[var(--warning)]">已截断到 {answer.max_rows} 行</span> : null}
            </div>
          </div>

          {answer.explanation ? (
            <p className="mt-2 text-xs leading-relaxed text-[var(--foreground)]">{answer.explanation}</p>
          ) : null}

          <pre className="mt-3 overflow-x-auto rounded-[10px] border border-[var(--border)] bg-[var(--secondary)] px-3 py-2 text-[11px] leading-relaxed text-[var(--foreground)]">
            <code>{answer.sql}</code>
          </pre>

          {answer.assumptions.length > 0 ? (
            <div className="mt-2">
              <p className="text-[11px] font-medium text-[var(--muted-foreground)]">模型的假设</p>
              <ul className="mt-1 space-y-0.5 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                {answer.assumptions.map((item, index) => (
                  <li key={index}>· {item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-3 max-h-[420px] overflow-auto rounded-[10px] border border-[var(--border)]">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-[var(--secondary)]">
                <tr>
                  {answer.columns.map((column) => (
                    <th
                      key={column}
                      className="whitespace-nowrap px-2 py-1.5 font-medium text-[var(--foreground)]"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {answer.rows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="border-t border-[var(--border)]">
                    {row.map((cell, cellIndex) => (
                      <td
                        key={cellIndex}
                        className="whitespace-nowrap px-2 py-1.5 tabular-nums text-[var(--muted-foreground)]"
                      >
                        {cell === null ? "—" : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
                {answer.rows.length === 0 ? (
                  <tr>
                    <td
                      className="px-2 py-3 text-[var(--muted-foreground)]"
                      colSpan={answer.columns.length || 1}
                    >
                      SQL 执行成功，但这个窗口内没有数据。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
