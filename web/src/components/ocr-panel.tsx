"use client";

import { Check, Copy, ImageUp, Loader2, ScanText, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

type OcrResult = {
  text: string;
  lines: string[];
  model: string;
  usage: Record<string, number>;
};

const MAX_BYTES = 8 * 1024 * 1024;
const ACCEPTED = ["image/png", "image/jpeg", "image/webp", "image/bmp"];

/** 把文件读成 data URL；后端支持带前缀的 base64，省掉前端手工剥前缀。 */
function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.readAsDataURL(file);
  });
}

/** 渲染图片取字面板：上传或粘贴图片，调用同源代理接口，展示并复制识别结果。 */
export function OcrPanel() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<OcrResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // 预览用临时 objectURL，切换图片或卸载时立即回收，避免浏览器长期占用内存。
  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const accept = useCallback((next: File | null) => {
    setResult(null);
    setError(null);
    setCopied(false);
    if (!next) {
      setFile(null);
      return;
    }
    if (!ACCEPTED.includes(next.type)) {
      setFile(null);
      setError("只支持 PNG / JPEG / WebP / BMP 格式的图片。");
      return;
    }
    if (next.size > MAX_BYTES) {
      setFile(null);
      setError("图片超过 8 MB，请压缩后再试。");
      return;
    }
    setFile(next);
  }, []);

  async function recognize() {
    if (!file) {
      setError("请先选择或粘贴一张图片。");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const imageBase64 = await readAsDataUrl(file);
      const response = await fetch("/ocr/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_base64: imageBase64, mime_type: file.type }),
      });
      const data = (await response.json().catch(() => null)) as (OcrResult & { error?: string }) | null;
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("请先登录后再使用图片取字。");
        }
        throw new Error(data?.error ?? "图片取字失败，请稍后重试。");
      }
      setResult(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "图片取字失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  async function copyText() {
    if (!result?.text) return;
    try {
      await navigator.clipboard.writeText(result.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("浏览器拒绝了剪贴板写入，请手动选中文本复制。");
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="rounded-md border border-slate-200 bg-white p-5">
        <h2 className="text-lg font-semibold text-slate-950">1. 选择图片</h2>
        <p className="mt-1 text-sm text-slate-500">支持点击选择、拖拽，或直接 Ctrl+V 粘贴截图。</p>

        <div
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            accept(event.dataTransfer.files?.[0] ?? null);
          }}
          onPaste={(event) => {
            const pasted = Array.from(event.clipboardData.files)[0];
            if (pasted) accept(pasted);
          }}
          tabIndex={0}
          className="mt-4 flex min-h-[220px] flex-col items-center justify-center gap-3 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center outline-none focus:border-cyan-600"
        >
          {preview ? (
            // 用原生 img 展示本地 objectURL，避免 next/image 走优化管线去请求不存在的域名。
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt="待识别图片预览" className="max-h-64 w-auto rounded border border-slate-200 bg-white object-contain" />
          ) : (
            <>
              <ImageUp className="h-7 w-7 text-slate-400" aria-hidden="true" />
              <span className="text-sm font-medium text-slate-700">拖入图片，或点击下面的按钮选择</span>
              <span className="text-xs text-slate-500">单张不超过 8 MB</span>
            </>
          )}
        </div>

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(",")}
          className="hidden"
          onChange={(event) => accept(event.target.files?.[0] ?? null)}
        />

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={loading}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
          >
            <ImageUp className="h-4 w-4" aria-hidden="true" />
            选择图片
          </button>
          <button
            type="button"
            onClick={recognize}
            disabled={loading || !file}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-slate-950 px-3 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ScanText className="h-4 w-4" aria-hidden="true" />}
            开始取字
          </button>
          <button
            type="button"
            onClick={() => accept(null)}
            disabled={loading || (!file && !result)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            清空
          </button>
        </div>

        {error ? <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p> : null}
      </section>

      <section className="rounded-md border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-slate-950">2. 识别结果</h2>
            <p className="mt-1 text-sm text-slate-500">
              {result ? `${result.lines.length} 行 / ${result.text.length} 字` : "识别结果会出现在这里，可直接复制。"}
            </p>
          </div>
          <button
            type="button"
            onClick={copyText}
            disabled={!result?.text}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
          >
            {copied ? <Check className="h-4 w-4 text-emerald-600" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
            {copied ? "已复制" : "复制全文"}
          </button>
        </div>

        <textarea
          readOnly
          value={result?.text ?? ""}
          placeholder={loading ? "正在识别…" : "还没有结果"}
          className="mt-4 h-[320px] w-full resize-none rounded-md border border-slate-200 bg-slate-50 p-3 font-mono text-sm leading-6 text-slate-800 outline-none"
        />

        {result ? <p className="mt-2 text-xs text-slate-400">模型：{result.model}</p> : null}
      </section>
    </div>
  );
}
