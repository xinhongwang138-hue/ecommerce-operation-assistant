import { LockKeyhole } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type LoginPageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

/** 渲染登录页面；在服务端加载业务数据，并把交互交给对应表单或客户端组件。 */
export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = (await searchParams) ?? {};
  const error = Array.isArray(params.error) ? params.error[0] : params.error;
  // 登录失败按原因区分文案：超时说明后端不可达，过期说明会话失效，其余按账号或密码错误处理。
  const errorMessage =
    error === "timeout"
      ? "登录请求超时（5 秒内未响应），后端服务可能不可用，请稍后重试。"
      : error === "expired"
        ? "登录状态已过期，请重新登录。"
        : error
          ? "邮箱或密码不正确，或者账号已停用。"
          : null;

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--bg)] px-5 py-10 text-[var(--foreground)]">
      <section className="card w-full max-w-md p-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[10px] bg-gradient-primary text-white shadow-[var(--shadow-btn)]">
            <LockKeyhole className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-medium text-[var(--primary)]">Ecommerce Operation Assistant</p>
            <h1 className="text-xl font-semibold">登录工作台</h1>
          </div>
        </div>

        {errorMessage ? (
          <div
            role="alert"
            className="mt-5 rounded-[10px] border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]"
          >
            {errorMessage}
          </div>
        ) : null}

        <form action="/login/submit" method="post" className="mt-6 space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-[var(--foreground)]">账号</span>
            <Input
              name="email"
              type="text"
              required
              className="mt-2 h-11"
              placeholder="Wxh15987456"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-[var(--foreground)]">密码</span>
            <Input
              name="password"
              type="password"
              required
              className="mt-2 h-11"
              placeholder="输入密码"
            />
          </label>
          <Button
            type="submit"
            size="lg"
            className="h-11 w-full bg-gradient-to-br from-[#6d5ae6] to-[#8b5cf6] text-white shadow-[var(--shadow-btn)] hover:opacity-90"
          >
            登录
          </Button>
        </form>

        <Link href="/" className="mt-4 inline-flex text-sm text-[var(--muted-foreground)] hover:text-[var(--primary)]">
          返回首页
        </Link>
      </section>
    </main>
  );
}
