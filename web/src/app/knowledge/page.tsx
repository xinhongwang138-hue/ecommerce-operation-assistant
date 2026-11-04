import { BookOpen } from "lucide-react";

import { KnowledgePanel } from "@/components/knowledge-panel";
import { KnowledgeSearchPanel } from "@/components/knowledge-search-panel";
import { getKnowledgeDocuments } from "@/lib/api";

/** 渲染掌柜智库页面；首屏列表由服务端取，交互交给客户端面板，请求一律走同源代理。 */
export default async function KnowledgePage() {
  const documents = await getKnowledgeDocuments();
  return (
    <main className="flex flex-col gap-6">
      <header className="card p-5">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <BookOpen className="h-6 w-6" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-medium text-[var(--primary)]">Knowledge Base</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">掌柜智库</h1>
            <p className="mt-2 text-sm text-slate-500">
              产品手册、话术与规范资料在这里入库。Agent 生成主图方案和卖点时会检索本工作区的资料，
              并在证据点里标出引用了哪份文档的哪一段。
            </p>
          </div>
        </div>
      </header>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <KnowledgePanel initialDocuments={documents} />
        <KnowledgeSearchPanel />
      </div>
    </main>
  );
}
