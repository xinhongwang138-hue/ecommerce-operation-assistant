"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { FileText, Loader2, Search, Trash2, Upload } from "lucide-react";

import type { RagDocument, RagIngestResult, RagSearchResult, RagStats } from "@/types";

const SAMPLE_QUESTION = "低糖饼干的库存和投放效率怎么样？";

/**
 * 检索面板。
 *
 * 每条命中都显示"哪一路召回的第几名"：只给一个总分的话，用户没法判断这条结果为什么
 * 排在这里——而 RAG 的信任问题几乎都在那一步。
 */
export function RagPanel({
  documents,
  stats,
}: {
  documents: RagDocument[];
  stats: RagStats | null;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<RagSearchResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestMessage, setIngestMessage] = useState<string | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);

  async function search(text: string) {
    const trimmed = text.trim();
    if (!trimmed) {
      setSearchError("写一个检索词，例如「库存预警」");
      return;
    }
    setSearching(true);
    setSearchError(null);
    setResult(null);
    try {
      const response = await fetch("/rag/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmed, top_k: 6 }),
      });
      const payload = (await response.json()) as RagSearchResult | { error?: string };
      if (!response.ok) {
        setSearchError(("error" in payload && payload.error) || `检索失败（${response.status}）`);
        return;
      }
      setResult(payload as RagSearchResult);
    } catch {
      setSearchError("请求未送达，请确认后端已启动");
    } finally {
      setSearching(false);
    }
  }

  async function ingest() {
    if (!title.trim() || !content.trim()) {
      setIngestError("标题与正文都不能为空");
      return;
    }
    setIngesting(true);
    setIngestError(null);
    setIngestMessage(null);
    try {
      const response = await fetch("/rag/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, content, source_kind: "text" }),
      });
      const payload = (await response.json()) as RagIngestResult | { error?: string };
      if (!response.ok) {
        setIngestError(("error" in payload && payload.error) || `入库失败（${response.status}）`);
        return;
      }
      const created = payload as RagIngestResult;
      const warning = created.warnings.length > 0 ? `｜${created.warnings[0]}` : "";
      setIngestMessage(
        `已入库《${created.document.title}》：${created.document.chunk_count} 片，${created.document.char_count} 字${warning}`,
      );
      setTitle("");
      setContent("");
      router.refresh();
    } catch {
      setIngestError("请求未送达，请确认后端已启动");
    } finally {
      setIngesting(false);
    }
  }

  async function remove(id: number) {
    const response = await fetch(`/rag/documents?id=${id}`, { method: "DELETE" }).catch(() => null);
    if (!response || !response.ok) {
      setIngestError("删除失败，请刷新后重试");
      return;
    }
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Search className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h2 className="text-base font-semibold text-[var(--foreground)]">检索</h2>
          </div>
          <span className="text-[11px] text-[var(--muted-foreground)]">
            {stats?.embedding_configured
              ? `向量已启用：${stats.embedding_model}，覆盖 ${(stats.embedding_coverage * 100).toFixed(0)}%`
              : "向量未配置：当前仅词法召回（BM25），仍然可用"}
          </span>
        </div>

        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            className="input h-10 flex-1 text-sm"
            placeholder={SAMPLE_QUESTION}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                search(query);
              }
            }}
          />
          <button
            className="btn-primary h-10 px-4 text-xs"
            onClick={() => search(query)}
            disabled={searching}
            type="button"
          >
            {searching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {searching ? "检索中…" : "检索"}
          </button>
        </div>

        {searchError ? (
          <p className="mt-3 rounded-[10px] border border-[var(--danger)] bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
            {searchError}
          </p>
        ) : null}

        {result ? (
          <div className="mt-3">
            <p className="text-[11px] text-[var(--muted-foreground)]">
              {result.hits.length} 条命中 · 词法 {result.lexical_hits} / 向量 {result.vector_hits} ·{" "}
              {result.elapsed_ms} ms · {result.vector_enabled ? "混合召回" : "仅词法"}
            </p>
            {result.warnings.length > 0 ? (
              <p className="mt-1 text-[11px] leading-relaxed text-[var(--warning)]">
                {result.warnings.join("；")}
              </p>
            ) : null}

            <div className="mt-2 space-y-2">
              {result.hits.map((hit) => (
                <div
                  key={`${hit.citation.document_id}-${hit.citation.ordinal}`}
                  className="rounded-[10px] border border-[var(--border)] p-3"
                >
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="font-medium text-[var(--foreground)]">
                      《{hit.citation.document_title}》
                    </span>
                    <span className="text-[var(--muted-foreground)]">
                      第 {hit.citation.ordinal + 1} 片 · 字符 {hit.citation.char_start}–
                      {hit.citation.char_end}
                    </span>
                    <span className="badge-gradient badge-muted tabular-nums">
                      RRF {hit.score.toFixed(3)}
                    </span>
                    {hit.lexical_rank ? (
                      <span className="text-[var(--muted-foreground)]">词法 #{hit.lexical_rank}</span>
                    ) : null}
                    {hit.vector_rank ? (
                      <span className="text-[var(--muted-foreground)]">向量 #{hit.vector_rank}</span>
                    ) : null}
                  </div>

                  <p className="mt-1.5 text-xs leading-relaxed text-[var(--foreground)]">{hit.snippet}</p>

                  {hit.matched_terms.length > 0 ? (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {hit.matched_terms.map((term) => (
                        <span
                          key={term}
                          className="rounded-[6px] bg-[var(--secondary)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]"
                        >
                          {term}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
              {result.hits.length === 0 ? (
                <p className="rounded-[10px] border border-[var(--border)] px-3 py-2 text-xs text-[var(--muted-foreground)]">
                  没有命中。换个说法，或先把相关文档入库——注意中文是按二元字组切分的，
                  「库存预警」能匹配「库存预警阈值」，但「库存不足」匹配不到「缺货」。
                </p>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Upload className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h2 className="text-base font-semibold text-[var(--foreground)]">入库一篇文本</h2>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
            粘贴运营复盘、话术、平台规则。入库时按句子切分并保留字符区间，检索命中后能指回原文位置。
            向量化失败不影响入库，只是这次只能词法召回。
          </p>

          <input
            className="input mt-3 h-9 w-full text-sm"
            placeholder="标题，例如：低糖饼干 10 月运营复盘"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <textarea
            className="input mt-2 h-32 w-full resize-y text-xs leading-relaxed"
            placeholder="正文…"
            value={content}
            onChange={(event) => setContent(event.target.value)}
          />
          <button
            className="btn-primary mt-2 h-9 px-4 text-xs"
            onClick={ingest}
            disabled={ingesting}
            type="button"
          >
            {ingesting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {ingesting ? "入库中…" : "入库"}
          </button>

          {ingestMessage ? (
            <p className="mt-2 rounded-[10px] border border-[var(--border)] bg-[var(--secondary)] px-3 py-2 text-[11px] leading-relaxed text-[var(--foreground)]">
              {ingestMessage}
            </p>
          ) : null}
          {ingestError ? (
            <p className="mt-2 rounded-[10px] border border-[var(--danger)] bg-[var(--danger-soft)] px-3 py-2 text-[11px] text-[var(--danger)]">
              {ingestError}
            </p>
          ) : null}
        </div>

        <div className="card p-5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
              <h2 className="text-base font-semibold text-[var(--foreground)]">已入库</h2>
            </div>
            <span className="text-[11px] text-[var(--muted-foreground)]">
              {stats?.documents ?? 0} 篇 · {stats?.chunks ?? 0} 片
            </span>
          </div>

          <div className="mt-3 max-h-80 space-y-2 overflow-auto">
            {documents.map((document) => (
              <div
                key={document.id}
                className="flex items-start justify-between gap-2 rounded-[10px] border border-[var(--border)] px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-[var(--foreground)]">{document.title}</p>
                  <p className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">
                    {document.chunk_count} 片 · {document.char_count} 字 ·{" "}
                    {document.embedded_chunks > 0
                      ? `向量 ${document.embedded_chunks}/${document.chunk_count}`
                      : "无向量"}
                  </p>
                </div>
                <button
                  className="btn-ghost h-7 shrink-0 px-2 text-[11px]"
                  onClick={() => remove(document.id)}
                  type="button"
                  aria-label={`删除 ${document.title}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
            {documents.length === 0 ? (
              <p className="text-xs text-[var(--muted-foreground)]">
                还没有文档。左边粘一段运营复盘试试，比如把上个月的投放复盘贴进来。
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
