import { redirect } from "next/navigation";

import { performLogin } from "@/lib/login-flow";

/**
 * 处理登录/提交请求：转发给后端换取 token，成功后写入 httpOnly cookie 并跳转工作台。
 * 具体流程在 `performLogin` 里，与演示快捷登录共用同一份实现。
 */
export async function POST(request: Request) {
  const formData = await request.formData();
  const outcome = await performLogin(
    formData.get("email")?.toString() ?? "",
    formData.get("password")?.toString() ?? "",
  );

  if (!outcome.ok) {
    redirect(outcome.reason === "timeout" ? "/login?error=timeout" : "/login?error=1");
  }

  redirect("/");
}
