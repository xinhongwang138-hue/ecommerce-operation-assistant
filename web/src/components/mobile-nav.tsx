"use client";

import { Bell, Bot, ClipboardList, LayoutDashboard, LogOut, Menu, ScanText, Settings, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ThemeToggle } from "./theme-toggle";

const NAV_ITEMS = [
  { href: "/", label: "工作台", icon: LayoutDashboard },
  { href: "/alerts", label: "待办中心", icon: Bell },
  { href: "/agent", label: "Agent 中心", icon: Bot },
  { href: "/imports", label: "导入中心", icon: ClipboardList },
  { href: "/ocr", label: "图片取字", icon: ScanText },
  { href: "/settings", label: "模型配置", icon: Settings },
];

type MobileNavProps = {
  userDisplayName: string | null;
};

/** 渲染 MobileNav 组件，提供移动端汉堡按钮与侧边抽屉导航。 */
export function MobileNav({ userDisplayName }: MobileNavProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="打开导航"
        className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] lg:hidden"
      >
        <Menu className="h-4 w-4" aria-hidden="true" />
      </button>

      {open ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col bg-[var(--sidebar-bg)] shadow-[var(--shadow-pop)]">
            <div className="flex items-center justify-between px-5 py-4">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-primary text-sm font-bold text-white">
                  EO
                </div>
                <span className="text-sm font-semibold text-[var(--foreground)]">电商运营助手</span>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭导航"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>

            <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setOpen(false)}
                    className="flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm font-medium text-[var(--muted-foreground)] transition hover:bg-[var(--primary-soft)] hover:text-[var(--primary)]"
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                    {item.label}
                  </Link>
                );
              })}
            </nav>

            <div className="space-y-3 border-t border-[var(--border)] px-5 py-4">
              <div className="flex items-center justify-between">
                <ThemeToggle />
                {userDisplayName ? (
                  <form action="/logout" method="post" className="inline-flex items-center gap-2">
                    <span className="max-w-28 truncate text-xs text-[var(--muted-foreground)]">{userDisplayName}</span>
                    <button
                      type="submit"
                      aria-label="退出登录"
                      className="inline-flex h-8 items-center justify-center gap-1.5 rounded-[10px] border border-[var(--border)] bg-[var(--card)] px-2.5 text-xs font-medium text-[var(--foreground)] hover:bg-[var(--muted)]"
                    >
                      <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                      退出
                    </button>
                  </form>
                ) : (
                  <Link
                    href="/login"
                    className="inline-flex h-8 items-center rounded-[10px] bg-gradient-primary px-3 text-xs font-semibold text-white"
                  >
                    登录
                  </Link>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
