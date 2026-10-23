import {
  ArrowRight,
  Boxes,
  Building2,
  ClipboardList,
  Database,
  Download,
  FileText,
  Globe,
  ImageIcon,
  Megaphone,
  Package,
  Store as StoreIcon,
  Store as StoreStatIcon,
  Package as ProductStatIcon,
  Layers,
} from "lucide-react";
import Link from "next/link";

import { DashboardCharts } from "@/components/dashboard-charts";
import { StatCard } from "@/components/ui";
import { ProductCreateForm } from "@/components/product-create-form";
import { StoreCreateForm } from "@/components/store-create-form";
import { getCurrentUser, getProducts, getStores, isBackendAvailable } from "@/lib/api";
import { getAuthToken } from "@/lib/session";
import type { Platform, Product, Store } from "@/types";

const platformLabels: Record<Platform, string> = {
  douyin: "抖音",
  taobao: "淘宝",
  tmall: "天猫",
  pinduoduo: "拼多多",
  jd: "京东",
  tiktok_shop: "TikTok",
  amazon: "Amazon",
  other: "其他",
};

const platformFilters: Array<{ value: "all" | Platform; label: string }> = [
  { value: "all", label: "全部平台" },
  { value: "douyin", label: "抖音" },
  { value: "taobao", label: "淘宝" },
  { value: "tmall", label: "天猫" },
  { value: "pinduoduo", label: "拼多多" },
  { value: "jd", label: "京东" },
];

const workflow = [
  { title: "建店建档", icon: Building2, tone: "bg-gradient-primary", href: "/" },
  { title: "导入数据", icon: ClipboardList, tone: "bg-gradient-info", href: "/imports" },
  { title: "补商品信息", icon: ImageIcon, tone: "bg-gradient-success", href: "/?workflow=product" },
  { title: "投放建议", icon: Megaphone, tone: "bg-gradient-warning", href: "/?workflow=ads" },
  { title: "周期复盘", icon: FileText, tone: "bg-gradient-danger", href: "/?workflow=review" },
];

type HomeProps = {
  searchParams?: Promise<{ platform?: string; store?: string; demo?: string }>;
};

/** 渲染首页页面；在服务端加载业务数据，并把交互交给对应表单或客户端组件。 */
export default async function Home({ searchParams }: HomeProps) {
  const query = searchParams ? await searchParams : {};
  const selectedPlatform = parsePlatform(query.platform);
  const selectedStoreId = parseStoreId(query.store);
  // 首页聚合商品、店铺和当前用户权限；写操作入口只在 canEdit 为 true 时展示。
  const [products, stores, token] = await Promise.all([getProducts(), getStores(), getAuthToken()]);
  const currentUser = token ? await getCurrentUser(token) : null;
  const canEdit = Boolean(currentUser?.permissions.can_edit);
  // 已登录但数据全空时先探测后端：后端不可达会表现为"空数据"，需明确提示而不是静默展示空卡片。
  const backendUnavailable =
    token !== null && products.length === 0 && stores.length === 0 ? !(await isBackendAvailable()) : false;

  const storeById = new Map(stores.map((store) => [store.id, store]));
  const productsByStore = countProductsByStore(products);
  const activeStores = stores.filter((store) => store.status === "active").length;
  const platformCount = new Set([...stores.map((store) => store.platform), ...products.map((product) => product.platform)]).size;
  const unboundProducts = products.filter((product) => product.store_id === null).length;
  const workflowItems = buildHomeWorkflow(products, stores, unboundProducts);
  const filteredProducts = products.filter((product) => {
    if (selectedPlatform !== "all" && product.platform !== selectedPlatform) {
      return false;
    }
    if (selectedStoreId !== null && product.store_id !== selectedStoreId) {
      return false;
    }
    return true;
  });

  // 仪表盘图表数据：近 7 日新增资产趋势 + 平台分布
  const trendData = buildGrowthTrend(products, stores);
  const ringData = buildPlatformDistribution(products, stores);

  return (
    <main className="flex flex-col gap-6">
      {backendUnavailable ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-[10px] border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-4 py-3 text-sm text-[var(--danger)] sm:flex-row sm:items-center sm:justify-between"
        >
          <span>
            后端服务暂时不可用（请求已超时中止，默认 4 秒），页面数据可能不完整。请确认 8020 后端已启动后刷新重试。
          </span>
          <a href="/" className="shrink-0 font-medium underline underline-offset-4">
            重新加载
          </a>
        </div>
      ) : null}
      {/* 页头：标题 + 渐变统计卡 */}
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-semibold text-[var(--primary)]">Ecommerce Operation Assistant</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-[var(--foreground)] sm:text-3xl">
            多平台店铺运营工作台
          </h1>
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">聚合店铺、商品与 AI 运营任务，一站掌握经营链路</p>
        </div>
        <div className="grid w-full grid-cols-2 gap-3 sm:w-[560px] sm:grid-cols-4">
          <StatCard label="店铺" value={stores.length} icon={StoreStatIcon} gradient="bg-gradient-primary" />
          <StatCard label="活跃店铺" value={activeStores} icon={Layers} gradient="bg-gradient-info" />
          <StatCard label="商品" value={products.length} icon={ProductStatIcon} gradient="bg-gradient-success" />
          <StatCard label="平台" value={platformCount} icon={Globe} gradient="bg-gradient-warning" />
        </div>
      </header>

      {/* 仪表盘图表 */}
      <DashboardCharts
        trend={trendData}
        ring={ringData}
        trendTitle="资产增长趋势"
        trendSubtitle="近 7 日新增店铺与商品数"
        ringTitle="平台分布"
        ringSubtitle="店铺与商品平台占比"
        badgeText="近 7 天"
      />

      <section className="card p-4 lg:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-[var(--foreground)]">演示和导入</h2>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              用演示数据快速跑通店铺、库存、投放实验和复盘链路，或下载绩效数据导入模板。
            </p>
            {query.demo === "created" ? (
              <p className="mt-2 text-xs font-medium text-[var(--success)]">演示数据已准备好。</p>
            ) : null}
            {query.demo === "failed" ? (
              <p className="mt-2 text-xs font-medium text-[var(--danger)]">演示数据创建失败，请确认当前账号有编辑权限。</p>
            ) : null}
            {!canEdit ? (
              <p className="mt-2 text-xs font-medium text-[var(--warning)]">当前账号为只读权限，只能查看数据，不能创建或导入。</p>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            {canEdit ? (
              <form action="/workspace/demo-data" method="post">
                <button type="submit" className="btn-ghost h-9 px-3 text-sm">
                  <Database className="h-4 w-4" aria-hidden="true" />
                  生成演示数据
                </button>
              </form>
            ) : null}
            <Link href="/workspace/templates/performance-records" className="btn-ghost h-9 px-3 text-sm">
              <Download className="h-4 w-4" aria-hidden="true" />
              下载导入模板
            </Link>
            {canEdit ? (
              <Link href="/imports" className="btn-primary h-9 px-3 text-sm">
                <Package className="h-4 w-4" aria-hidden="true" />
                导入中心
              </Link>
            ) : null}
          </div>
        </div>
      </section>

      <section className={`grid gap-6 ${canEdit ? "xl:grid-cols-[360px_1fr]" : ""}`}>
        {canEdit ? (
          <div className="flex flex-col gap-6">
            <div className="card p-5">
              <StoreCreateForm />
            </div>
            <div className="card p-5">
              <ProductCreateForm stores={stores} />
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-6">
          <section className="card p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-base font-semibold text-[var(--foreground)]">店铺视图</h2>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">按平台和店铺归拢商品，先把多平台运营入口铺出来</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {platformFilters.map((platform) => (
                  <Link
                    key={platform.value}
                    href={platform.value === "all" ? "/" : `/?platform=${platform.value}`}
                    className={`inline-flex h-9 items-center rounded-[10px] border px-3 text-sm font-medium transition ${
                      selectedPlatform === platform.value
                        ? "border-transparent bg-gradient-primary text-white shadow-[var(--shadow-btn)]"
                        : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                    }`}
                  >
                    {platform.label}
                  </Link>
                ))}
              </div>
            </div>

            <div className="mt-5 grid gap-3 lg:grid-cols-2">
              {stores.length === 0 ? (
                <div className="rounded-[10px] border border-dashed border-[var(--border-strong)] px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">
                  还没有店铺，先在左侧创建一个平台店铺。
                </div>
              ) : (
                stores.map((store) => (
                  <StoreSummaryCard
                    key={store.id}
                    store={store}
                    productCount={productsByStore.get(store.id) ?? 0}
                    selected={store.id === selectedStoreId}
                  />
                ))
              )}
              {unboundProducts > 0 ? (
                <div className="rounded-[10px] border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-4 py-4">
                  <div className="flex items-center gap-3">
                    <Package className="h-5 w-5 text-[var(--warning)]" aria-hidden="true" />
                    <div>
                      <p className="text-sm font-semibold text-[var(--foreground)]">未绑定店铺商品</p>
                      <p className="mt-1 text-xs text-[var(--warning)]">{unboundProducts} 个商品需要补店铺归属</p>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          </section>

          <section className="card p-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-[var(--foreground)]">商品池</h2>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">围绕每个商品补竞品、出素材、做投放实验和复盘</p>
              </div>
              <span className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[var(--primary-soft)] px-3 text-xs font-semibold text-[var(--primary)]">
                <Boxes className="h-3.5 w-3.5" aria-hidden="true" />
                {filteredProducts.length} / {products.length}
              </span>
            </div>

            <div className="mt-5 overflow-hidden rounded-[10px] border border-[var(--border)]">
              <div className="hidden grid-cols-[1.3fr_0.9fr_0.8fr_0.7fr_1fr_96px] bg-[var(--card-muted)] px-4 py-3 text-xs font-semibold text-[var(--muted-foreground)] lg:grid">
                <span>商品</span>
                <span>店铺</span>
                <span>平台</span>
                <span>售价</span>
                <span>卖点</span>
                <span>动作</span>
              </div>

              {filteredProducts.length === 0 ? (
                <div className="px-4 py-12 text-center text-sm text-[var(--muted-foreground)]">
                  当前筛选下没有商品，可以新建商品或切换平台。
                </div>
              ) : (
                <div className="divide-y divide-[var(--border)]">
                  {filteredProducts.map((product) => (
                    <ProductRow key={product.id} product={product} store={product.store_id ? storeById.get(product.store_id) : null} />
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-5">
        {workflow.map((item) => {
          const Icon = item.icon;
          const workflowItem = workflowItems.get(item.title);
          return (
            <Link
              key={item.title}
              href={workflowItem?.href ?? item.href}
              className="card card-hover group p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className={`flex h-10 w-10 items-center justify-center rounded-[10px] text-white shadow-[var(--shadow-btn)] ${item.tone}`}>
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </div>
                <span className="rounded-full bg-[var(--card-muted)] px-2 py-1 text-xs font-semibold text-[var(--muted-foreground)]">
                  {workflowItem?.count ?? 0}
                </span>
              </div>
              <h3 className="mt-4 text-sm font-semibold text-[var(--foreground)]">{item.title}</h3>
              <p className="mt-1 text-xs leading-5 text-[var(--muted-foreground)]">{workflowItem?.hint ?? "查看下一步"}</p>
            </Link>
          );
        })}
      </section>
    </main>
  );
}

/** 构建近7日资产增长趋势，聚合店铺与商品的创建时间。 */
function buildGrowthTrend(products: Product[], stores: Store[]): Array<{ date: string; count: number }> {
  const days: Array<{ date: string; count: number }> = [];
  const now = new Date();
  for (let i = 6; i >= 0; i -= 1) {
    const day = new Date(now);
    day.setDate(now.getDate() - i);
    const key = `${day.getMonth() + 1}/${day.getDate()}`;
    const start = new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
    const end = start + 86400000;
    let count = 0;
    for (const product of products) {
      const time = new Date(product.created_at).getTime();
      if (time >= start && time < end) count += 1;
    }
    for (const store of stores) {
      const time = new Date(store.created_at).getTime();
      if (time >= start && time < end) count += 1;
    }
    days.push({ date: key, count });
  }
  return days;
}

/** 构建平台分布，聚合店铺与商品按平台统计。 */
function buildPlatformDistribution(products: Product[], stores: Store[]): Array<{ name: string; value: number }> {
  const counts = new Map<string, number>();
  for (const store of stores) {
    const label = platformLabels[store.platform];
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  for (const product of products) {
    const label = platformLabels[product.platform];
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);
}

/** 构建首页工作流，从现有业务数据推导稳定的页面展示模型，不在渲染阶段修改源数据。 */
function buildHomeWorkflow(products: Product[], stores: Store[], unboundProducts: number): Map<string, { count: number; hint: string; href: string }> {
  // 工作流卡片只用已有数据提示下一步，不自动创建诊断、素材或投放实验。
  const missingSellingPoints = products.filter((product) => product.selling_points.length === 0).length;
  const missingPlatformIds = products.filter((product) => !product.external_product_id).length;
  const productsReadyForOps = products.filter((product) => product.store_id !== null && product.selling_points.length > 0).length;

  return new Map([
    [
      "建店建档",
      {
        count: stores.length + products.length,
        hint: unboundProducts > 0 ? `${unboundProducts} 个商品待绑定店铺` : "店铺和商品入口",
        href: "/",
      },
    ],
    [
      "导入数据",
      {
        count: missingPlatformIds,
        hint: missingPlatformIds > 0 ? `${missingPlatformIds} 个商品待补平台 ID` : "导入商品/SKU/经营数据",
        href: "/imports",
      },
    ],
    [
      "补商品信息",
      {
        count: missingSellingPoints,
        hint: missingSellingPoints > 0 ? `${missingSellingPoints} 个商品待补卖点` : "进入单品运营闭环",
        href: firstProductHref(products, "overview"),
      },
    ],
    [
      "投放建议",
      {
        count: productsReadyForOps,
        hint: productsReadyForOps > 0 ? "从素材和链接生成投放建议" : "先补商品基础信息",
        href: firstProductHref(products, "ads"),
      },
    ],
    [
      "周期复盘",
      {
        count: products.length,
        hint: products.length > 0 ? "回填经营数据后生成复盘" : "先创建商品",
        href: firstProductHref(products, "review"),
      },
    ],
  ]);
}

/** 首个商品链接，从现有业务数据推导稳定的页面展示模型，不在渲染阶段修改源数据。 */
function firstProductHref(products: Product[], tab: string): string {
  return products[0] ? `/products/${products[0].id}?tab=${tab}` : "/";
}

/** 渲染 StoreSummaryCard 组件，集中展示店铺汇总卡片所需的数据、状态和操作入口。 */
function StoreSummaryCard({
  store,
  productCount,
  selected,
}: {
  store: Store;
  productCount: number;
  selected: boolean;
}) {
  return (
    <div
      className={`card card-hover p-4 ${
        selected ? "border-[var(--primary)] bg-[var(--primary-soft)]" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <StoreIcon className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[var(--foreground)]">{store.name}</p>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">{platformLabels[store.platform]}</p>
          </div>
        </div>
        <span
          className={`badge-gradient ${
            store.status === "active" ? "badge-success" : store.status === "paused" ? "badge-warning" : "badge-muted"
          }`}
        >
          {store.status === "active" ? "启用" : store.status === "paused" ? "暂停" : "关闭"}
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-xs text-[var(--muted-foreground)]">商品数</p>
          <p className="mt-1 font-semibold text-[var(--foreground)]">{productCount}</p>
        </div>
        <div>
          <p className="text-xs text-[var(--muted-foreground)]">负责人</p>
          <p className="mt-1 truncate font-semibold text-[var(--foreground)]">{store.owner_name ?? "-"}</p>
        </div>
      </div>
      <div className="mt-4 flex gap-2">
        <Link
          href={`/?store=${store.id}`}
          className="btn-ghost h-8 px-3 text-xs"
        >
          筛选商品
        </Link>
        <Link
          href={`/stores/${store.id}`}
          className="btn-primary h-8 px-3 text-xs"
        >
          店铺详情
        </Link>
      </div>
    </div>
  );
}

/** 渲染 ProductRow 组件，集中展示商品行所需的数据、状态和操作入口。 */
function ProductRow({ product, store }: { product: Product; store: Store | null | undefined }) {
  return (
    <div className="grid gap-3 px-4 py-4 text-sm lg:min-h-16 lg:grid-cols-[1.3fr_0.9fr_0.8fr_0.7fr_1fr_96px] lg:items-center lg:gap-0 lg:py-3">
      <div>
        <p className="font-medium text-[var(--foreground)]">{product.name}</p>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">{product.category ?? "未分类"}</p>
      </div>
      <span className="text-[var(--muted-foreground)]">{store?.name ?? "未绑定"}</span>
      <span className="text-[var(--muted-foreground)]">{platformLabels[product.platform]}</span>
      <span className="font-medium text-[var(--foreground)]">{product.price ? `¥${product.price}` : "-"}</span>
      <div className="flex flex-wrap gap-1.5">
        {product.selling_points.slice(0, 3).map((point) => (
          <span key={point} className="rounded-full bg-[var(--success)]/10 px-2 py-1 text-xs font-medium text-[var(--success)]">
            {point}
          </span>
        ))}
      </div>
      <Link
        href={`/products/${product.id}`}
        className="btn-ghost inline-flex h-9 w-24 items-center justify-center gap-1.5 px-3 text-xs"
      >
        进入
        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      </Link>
    </div>
  );
}

/** 统计商品by店铺，封装当前页面重复使用的转换或展示规则，并统一处理空值边界。 */
function countProductsByStore(products: Product[]): Map<number, number> {
  return products.reduce((counts, product) => {
    if (product.store_id !== null) {
      counts.set(product.store_id, (counts.get(product.store_id) ?? 0) + 1);
    }
    return counts;
  }, new Map<number, number>());
}

/** 解析平台，把不可信表单或 JSON 值转换为页面逻辑可安全使用的类型。 */
function parsePlatform(value: string | undefined): "all" | Platform {
  // URL 筛选参数做白名单校验，避免无效平台值影响服务端渲染结果。
  if (!value || value === "all") {
    return "all";
  }
  return Object.hasOwn(platformLabels, value) ? (value as Platform) : "all";
}

/** 解析店铺ID，把不可信表单或 JSON 值转换为页面逻辑可安全使用的类型。 */
function parseStoreId(value: string | undefined): number | null {
  if (!value) {
    return null;
  }
  const storeId = Number(value);
  return Number.isInteger(storeId) && storeId > 0 ? storeId : null;
}
