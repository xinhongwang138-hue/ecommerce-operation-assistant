import { Skeleton, SkeletonCard } from "@/components/ui";

/** 渲染页面级加载骨架屏，路由切换时替代整页内容展示。 */
export default function Loading() {
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="card p-4">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="mt-3 h-7 w-1/2" />
            <Skeleton className="mt-2 h-3 w-2/3" />
          </div>
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.6fr)]">
        <SkeletonCard lines={6} />
        <div className="grid content-start gap-6">
          <SkeletonCard lines={4} />
          <SkeletonCard lines={4} />
        </div>
      </div>
    </div>
  );
}
