import { ScanText } from "lucide-react";

import { OcrPanel } from "@/components/ocr-panel";

const scenes = [
  { title: "竞品详情页", detail: "截图对手的卖点、规格、活动文案，直接转成可编辑文本做对比。" },
  { title: "包装与标牌", detail: "把产品包装上的成分、净含量、执行标准取出来，补进商品档案。" },
  { title: "买家评价截图", detail: "把长图里的评价文字转成文本，喂给复盘环节做词频与差评归因。" },
];

/** 渲染图片取字页面；识别交互交给客户端面板，页面本身保持无状态。 */
export default function OcrPage() {
  return (
    <main className="flex flex-col gap-6">
      <div className="flex flex-col gap-6">
        <header className="card p-5">
          <div className="flex items-start gap-4">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
              <ScanText className="h-6 w-6" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-medium text-[var(--primary)]">Image to Text</p>
              <h1 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">图片取字</h1>
              <p className="mt-2 text-sm text-slate-500">
                把图片里的文字直接取成可编辑文本，用于抄录竞品文案、补齐商品规格、整理评价截图。
              </p>
            </div>
          </div>
        </header>

        <OcrPanel />

        <section className="grid gap-4 md:grid-cols-3">
          {scenes.map((scene) => (
            <div key={scene.title} className="card p-4">
              <p className="text-sm font-semibold text-slate-900">{scene.title}</p>
              <p className="mt-1 text-sm text-slate-500">{scene.detail}</p>
            </div>
          ))}
        </section>
      </div>
    </main>
  );
}
