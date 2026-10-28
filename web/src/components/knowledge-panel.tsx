"use client";

import { FileUp, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Progress, StatusBadge } from "@/components/ui";
import type { KnowledgeDocument, KnowledgeDocumentStatus } from "@/types";

const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;
const ACCEPTED = [".pdf", ".md", ".markdown"];

type StageInfo = { label: string; tone: "muted" | "info" | "success" | "danger" | "running"; percent: number };

/** 状态 → 文案 / 色调 / 进度百分比。与后端 KnowledgeDocumentStatus 一一对应。 */
const STAGE_BY_STATUS: Record<KnowledgeDocumentStatus, StageInfo> = {
  pending: { label: "排队中", tone: "muted", percent: 5 },
  submitting: { label: "提交知识库", tone: "running", percent: 15 },
  parsing: { label: "解析文档", tone: "running", percent: 35 },
  splitting: { label: "切片中", tone: "running", percent: 55 },
  recognizing: { label: "识别商品主体", tone: "running", percent: 65 },
  embedding: { label: "向量化中", tone: "running", percent: 80 },
  importing: { label: "写入向量库", tone: "running", percent: 92 },
  ready: { label: "已入库", tone: "success", percent: 100 },
  failed: { label: "导入失败", tone: "danger", percent: 100 },
};

const ACTIVE_STATUSES = new Set<KnowledgeDocumentStatus>([
  "pending",
  "submitting",
  "parsing",
  "splitting",
  "recognizing",
  "embedding",
  "importing",
]);

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 掌柜智库资料面板：上传 + 逐阶段进度 + 资料列表。
 *
 * 进度用 2s 轮询而非 SSE：智库侧的进度本来就只存在它的进程内存里
 * （task_utils._TASK_STORE），电商这边以自家库的状态为准，轮询语义最直白，
 * 也避免为一个大分钟级任务再架一条长连接。
 */
export function KnowledgePanel({ initialDocuments }: { initialDocuments: KnowledgeDocument[] }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  // 首屏由服务端取数，客户端只负责后续轮询与交互，避免在 effect 里同步 setState
  const [documents, setDocuments] = useState<KnowledgeDocument[]>(initialDocuments);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    const response = await fetch("/knowledge/documents", { cache: "no-store" }).catch(() => null);
    if (!response) {
      setError("知识库服务不可达，请确认后端已启动。");
      setLoading(false);
      return;
    }
    if (response.status === 401) {
      setError("请先登录后再使用掌柜智库。");
      setDocuments([]);
      setLoading(false);
      return;
    }
    const data = (await response.json().catch(() => null)) as
      | KnowledgeDocument[]
      | { error?: string }
      | null;
    if (!response.ok || !Array.isArray(data)) {
      setError(Array.isArray(data) ? "资料列表加载失败" : (data?.error ?? "资料列表加载失败"));
      setLoading(false);
      return;
    }
    setDocuments(data);
    setError(null);
    setLoading(false);
  }, []);

  // 只要还有在途任务就继续轮询；全部到终态就自己停下，不留下空转的定时器
  useEffect(() => {
    const hasActive = documents.some((item) => ACTIVE_STATUSES.has(item.status));
    if (!hasActive) return;
    const timer = setInterval(() => void loadDocuments(), 2000);
    return () => clearInterval(timer);
  }, [documents, loadDocuments]);

  async function handleSubmit() {
    const file = inputRef.current?.files?.[0];
    if (!file) {
      setError("请先选择一份 PDF 或 Markdown 文档。");
      return;
    }
    const suffix = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
    if (!ACCEPTED.includes(suffix)) {
      setError(`只支持 ${ACCEPTED.join(" / ")}，当前文件是 ${suffix || "无后缀"}。`);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(`文件 ${(file.size / 1024 / 1024).toFixed(1)}MB，超过 ${MAX_UPLOAD_BYTES / 1024 / 1024}MB 上限。`);
      return;
    }

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/knowledge/documents", { method: "POST", body: form });
      const data = (await response.json().catch(() => null)) as
        | KnowledgeDocument
        | { error?: string }
        | null;
      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? "登录已过期，请重新登录。"
            : ((data as { error?: string })?.error ?? "上传失败，请稍后重试。"),
        );
      }
      const created = data as KnowledgeDocument;
      setNotice(`已受理「${created.title}」，开始解析。`);
      setDocuments((previous) => [created, ...previous]);
      if (inputRef.current) inputRef.current.value = "";
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "上传失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(document: KnowledgeDocument) {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/knowledge/documents/${document.id}`, { method: "DELETE" });
      if (!response.ok) {
        const data = (await response.json().catch(() => null)) as { error?: string } | null;
        throw new Error(data?.error ?? "删除失败。");
      }
      setDocuments((previous) => previous.filter((item) => item.id !== document.id));
      setNotice(`已删除「${document.title}」。注意：已入库的切片暂不随之下架，检索里可能仍会出现。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">资料库</h2>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            上传产品手册、话术与规范文档，入库后 Agent 生成文案时会引用并标注出处。
          </p>
        </div>
        <button type="button" className="btn-ghost" onClick={() => void loadDocuments()}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(",")}
          className="text-xs"
          aria-label="选择要上传的资料"
        />
        <button
          type="button"
          className="btn-primary"
          onClick={() => void handleSubmit()}
          disabled={busy}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <FileUp size={14} />}
          上传并入库
        </button>
      </div>

      {error ? (
        <p className="mt-2 rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-xs text-[var(--destructive)]">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="mt-2 rounded-md bg-[var(--muted)] px-3 py-2 text-xs text-[var(--muted-foreground)]">
          {notice}
        </p>
      ) : null}

      {loading ? (
        <p className="mt-4 text-xs text-[var(--muted-foreground)]">正在加载资料列表…</p>
      ) : documents.length === 0 ? (
        <div className="mt-4 rounded-[10px] border border-dashed border-[var(--border)] px-4 py-8 text-center text-xs text-[var(--muted-foreground)]">
          还没有资料。上传一份产品手册试试。
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-[var(--border)]">
          {documents.map((document) => {
            const stage = STAGE_BY_STATUS[document.status] ?? STAGE_BY_STATUS.pending;
            const active = ACTIVE_STATUSES.has(document.status);
            return (
              <li key={document.id} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{document.title}</p>
                    <p className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                      {document.original_filename} · {formatSize(document.size_bytes)} ·{" "}
                      {document.status_message ?? stage.label}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge label={stage.label} tone={stage.tone} />
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => void handleDelete(document)}
                      disabled={busy}
                      aria-label={`删除 ${document.title}`}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                {active ? <Progress value={stage.percent} className="mt-2" /> : null}
                {document.error_message ? (
                  <p className="mt-1 text-xs text-[var(--destructive)]">{document.error_message}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
