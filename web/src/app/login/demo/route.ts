import { redirect } from "next/navigation";

import { DEMO_ACCOUNT_EMAIL, demoLoginPassword } from "@/lib/demo-login";
import { performLogin } from "@/lib/login-flow";

/**
 * 演示快捷登录：口令由服务端从环境变量取出，浏览器只发一个空 POST。
 * 请求里不接受任何账号或口令参数，避免这个入口被当成"任意账号免密登录"使用。
 */
export async function POST() {
  const password = demoLoginPassword();
  if (!password) {
    redirect("/login?error=1");
  }

  const outcome = await performLogin(DEMO_ACCOUNT_EMAIL, password);
  if (!outcome.ok) {
    redirect(outcome.reason === "timeout" ? "/login?error=timeout" : "/login?error=1");
  }

  redirect("/");
}
