import { cookies } from "next/headers";

import { API_BASE_URL, fetchWithTimeout } from "@/lib/api";
import { AUTH_COOKIE_NAME } from "@/lib/session";

/** 登录失败的原因，用于回落到登录页时给出不同文案。 */
export type LoginFailureReason = "timeout" | "rejected";

export type LoginOutcome = { ok: true } | { ok: false; reason: LoginFailureReason };

/**
 * 用邮箱口令向后端换取 token，并写入服务端 httpOnly cookie。
 * 手动登录与演示快捷登录共用这一条路径，保证超时处理与 cookie 属性不会出现两套写法。
 */
export async function performLogin(email: string, password: string): Promise<LoginOutcome> {
  let response: Response;
  try {
    // 登录是同步等待的关键路径，超过 5 秒视为后端不可用，立即回落登录页而不是让表单一直挂起。
    response = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/auth/login`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      },
      5000,
    );
  } catch {
    return { ok: false, reason: "timeout" };
  }

  if (!response.ok) {
    return { ok: false, reason: "rejected" };
  }

  const payload = (await response.json()) as { access_token: string };
  const cookieStore = await cookies();
  // httpOnly cookie 降低 token 被客户端脚本读取的风险；后续 API 调用由服务端统一附加 Authorization。
  cookieStore.set(AUTH_COOKIE_NAME, payload.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
  });

  return { ok: true };
}
