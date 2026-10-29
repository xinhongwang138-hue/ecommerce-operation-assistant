"use client";

import { Loader2, Search } from "lucide-react";
import { useState } from "react";

import type { KnowledgeSearchResult, KnowledgeSource } from "@/types";

/**
 * 智库答案是 Markdown 文本，这里只做最小可读化。
 *
 * 不引入 markdown 渲染器：答案里的图片是 ObjectStorage 的内网地址，直接渲染成图片
 * 在其它机器上必然裂图，摊成裸 URL 又会把内网地址糊在界面上。统一收成「[图片]」，
 * 顺带去掉 ** 与反引号，避免用户看到字面的星号。
 */
function formatAnswer(text: string): string {
  const flattened = text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "[图片]")
    .replace(/https?:\/\/\S+?\.(?:png|jpe?g|webp|gif)\b/g, "[图片]")
    .replace(/【图片】/g, "[图片]")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1");

  // 一篇文档常带多张图，逐张摊开会把答案尾部撑成一片「[图片]」。
  // 这里按行收敛：连续（允许中间夹空行）的图片标记只留一个。
  const kept: string[] = [];
  for (const line of flattened.split("\n")) {
    if (line.trim() === "[图片]") {
      while (kept.length > 0 && kept[kept.length - 1].trim() === "") kept.pop();
      if (kept[kept.length - 1] !== "[图片]") kept.push("[图片]");
    } else {
      kept.push(line);
    }
  }
  return kept.join("\n").trim();
}

/**
 * 检索预览面板：验证"这份资料进库后确实能被查到"，并直接看到出处。
 *
 * 出处要能指到具体文档：file_title 是文档名，chunk_id 是切片主键，
 * parent_title 定位到章节。三者缺一，"引用了哪一段"就只是句空话。
 */
export function KnowledgeSearchPanel() {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSearch() {
    const trimmed = query.trim();
    if (!trimmed) {
      setError("请输入要检索的问题。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmed, limit: 5 }),
      });
      const data = (await response.json().catch(() => null)) as
        | KnowledgeSearchResult
        | { error?: string }
        | null;
      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? "请先登录后再使用掌柜智库。"
            : ((data as { error?: string })?.error ?? "检索失败，请稍后重试。"),
        );
      }
      const result = data as KnowledgeSearchResult;
      setAnswer(result.answer || "（没有生成答案）");
      setSources(result.sources ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "检索失败，请稍后重试。");
      setAnswer(null);
      setSources([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold">检索预览</h2>
      <p className="mt-1 text-xs text-[var(--muted-foreground)]">
        只能查到本工作区的资料。切换工作区后结果应完全不同，这是租户隔离的直观验证入口。
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void handleSearch();
          }}
          placeholder="例如：这台机器的熔带怎么更换？"
          className="min-w-[220px] flex-1 rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          aria-label="检索问题"
        />
        <button type="button" className="btn-primary" onClick={() => void handleSearch()} disabled={loading}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          检索
        </button>
      </div>

      {error ? (
        <p className="mt-3 rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-xs text-[var(--destructive)]">
          {error}
        </p>
      ) : null}

      {answer ? (
        <div className="mt-3 rounded-[10px] border border-[var(--border)] px-3 py-2">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">答案</p>
          <p className="mt-1 whitespace-pre-wrap text-sm">{formatAnswer(answer)}</p>
        </div>
      ) : null}

      {sources.length > 0 ? (
        <div className="mt-3">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">参考来源</p>
          <ol className="mt-2 space-y-2">
            {sources.map((source, index) => {
              // 出处指到"哪一章"：parent_title 是切片所属章节，title 只是切片首个标题行。
              // parent_title 缺失时（联网结果、老数据）回落到 title。
              const section = source.parent_title || source.title;
              return (
                <li key={`${source.chunk_id ?? index}`} className="rounded-[10px] border border-[var(--border)] px-3 py-2">
                  <p className="text-xs font-medium">
                    [{index + 1}] {source.file_title ?? source.title ?? "未知文档"}
                    {source.score !== null && source.score !== undefined ? (
                      <span className="ml-2 text-[var(--muted-foreground)]">
                        相关度 {source.score.toFixed(3)}
                      </span>
                    ) : null}
                  </p>
                  {section && section !== source.file_title ? (
                    <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">段落：{section}</p>
                  ) : null}
                  {source.snippet ? (
                    <p className="mt-1 line-clamp-2 text-xs">{source.snippet}</p>
                  ) : null}
                  {source.chunk_id ? (
                    <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">切片 {source.chunk_id}</p>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
