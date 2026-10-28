import { Bell, BookOpen, Bot, ClipboardList, LayoutDashboard, ScanText, Settings } from "lucide-react";
import Link from "next/link";

import { MobileNav } from "./mobile-nav";
import { ThemeToggle } from "./theme-toggle";
import { getCurrentUser } from "@/lib/api";
import { getAuthToken } from "@/lib/session";

const NAV_ITEMS = [
  { href: "/", label: "工作台", icon: LayoutDashboard, hint: "多平台店铺运营总览" },
  { href: "/alerts", label: "待办中心", icon: Bell, hint: "低库存与异常收口" },
  { href: "/agent", label: "Agent 中心", icon: Bot, hint: "运营智能体任务" },
  { href: "/imports", label: "导入中心", icon: ClipboardList, hint: "商品/经营数据导入" },
  { href: "/knowledge", label: "掌柜智库", icon: BookOpen, hint: "资料入库与检索引用" },
  { href: "/ocr", label: "图片取字", icon: ScanText, hint: "图片文字识别" },
  { href: "/settings", label: "模型配置", icon: Settings, hint: "模型与用户管理" },
];

/** 渲染 AppShell 组件，提供统一侧边栏导航、顶部工具栏与主内容区。 */
export async function AppShell({ children }: { children: React.ReactNode }) {
  const token = await getAuthToken();
  const currentUser = token ? await getCurrentUser(token) : null;
  const displayName = currentUser?.display_name ?? null;

  return (
    <div className="flex min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      {/* 桌面端侧边栏 */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-[var(--border)] bg-[var(--sidebar-bg)] lg:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-primary text-sm font-bold text-white shadow-[var(--shadow-btn)]">
            EO
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[var(--foreground)]">电商运营助手</p>
            <p className="text-[11px] text-[var(--muted-foreground)]">Ecommerce Operation Assistant</p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="group flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm font-medium text-[var(--muted-foreground)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--primary)]"
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] transition group-hover:border-transparent group-hover:bg-gradient-primary group-hover:text-white">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="min-w-0">
                  <span className="block">{item.label}</span>
                  <span className="block text-[11px] text-[var(--muted-foreground)] opacity-70">{item.hint}</span>
                </span>
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-[var(--border)] px-5 py-4">
          <p className="text-[11px] text-[var(--muted-foreground)]">蓝紫主题 · 浅色/深色可切换</p>
        </div>
      </aside>

      {/* 主区域 */}
      <div className="flex min-h-screen w-full flex-1 flex-col lg:pl-64">
        {/* 顶部工具栏 */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-[var(--border)] bg-[var(--topbar-bg)] px-4 backdrop-blur-md lg:px-8">
          <MobileNav userDisplayName={displayName} />
          <div className="flex-1" />
          <ThemeToggle />
          {displayName ? (
            <form action="/logout" method="post" className="inline-flex items-center gap-2">
              <span className="hidden max-w-36 items-center truncate rounded-full border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-xs font-medium text-[var(--muted-foreground)] sm:inline-flex">
                {displayName}
              </span>
              <button
                type="submit"
                className="btn-ghost h-9 px-3 text-xs"
              >
                退出
              </button>
            </form>
          ) : (
            <Link href="/login" className="btn-primary h-9 px-4 text-xs">
              登录
            </Link>
          )}
        </header>

        <main className="page-fade flex-1 px-5 py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
