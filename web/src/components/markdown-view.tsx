"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownViewProps = {
  content: string;
  className?: string;
  small?: boolean;
};

/** 渲染 MarkdownView 组件，把 Agent 输出文本渲染为格式化 Markdown。 */
export function MarkdownView({ content, className = "", small = false }: MarkdownViewProps) {
  return (
    <div
      className={`prose-sm max-w-none ${small ? "text-xs leading-5" : "text-sm leading-6"} text-[var(--card-foreground)] [&_a]:text-[var(--primary)] [&_a]:underline [&_code]:rounded [&_code]:bg-[var(--muted)] [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_pre]:overflow-x-auto [&_pre]:rounded-[10px] [&_pre]:bg-[var(--muted)] [&_pre]:p-3 [&_pre]:text-xs [&_pre]:leading-5 [&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:mt-3 [&_h3]:text-sm [&_h3]:font-medium [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1 [&_blockquote]:border-l-2 [&_blockquote]:border-[var(--primary)] [&_blockquote]:pl-3 [&_blockquote]:text-[var(--muted-foreground)] [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-[var(--border)] [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_th]:text-xs [&_td]:border [&_td]:border-[var(--border)] [&_td]:px-2 [&_td]:py-1.5 [&_td]:text-xs ${className}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
