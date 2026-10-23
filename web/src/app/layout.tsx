import type { Metadata } from "next";
import { cookies } from "next/headers";

import { AppShell } from "@/components/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "电商运营助手",
  description: "AI 单品运营工作台",
};

/** 渲染 RootLayout 组件，初始化双主题并套用统一 App Shell。 */
export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const cookieStore = await cookies();
  const theme = cookieStore.get("eo_theme")?.value === "dark" ? "dark" : "light";
  return (
    <html
      lang="zh-CN"
      className="h-full antialiased"
      data-theme={theme}
    >
      <body className="min-h-full">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
