import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { API_BASE_URL, fetchWithTimeout } from "@/lib/api";
import { AUTH_COOKIE_NAME } from "@/lib/session";

/** 处理登录/提交请求：读取表单和路径参数，调用后端 API，并按结果响应或重定向。 */
export async function POST(request: Request) {
  const formData = await request.formData();
  // 登录表单由服务端 route 转发给后端，成功后只把 token 写入 httpOnly cookie。
  let response: Response;
  try {
    // 登录是同步等待的关键路径，超过 5 秒视为后端不可用，立即回落登录页而不是让表单一直挂起。
    response = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/auth/login`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: formData.get("email")?.toString() ?? "",
          password: formData.get("password")?.toString() ?? "",
        }),
      },
      5000,
    );
  } catch {
    redirect("/login?error=timeout");
  }

  if (!response.ok) {
    redirect("/login?error=1");
  }

  const payload = (await response.json()) as { access_token: string };
  const cookieStore = await cookies();
  // httpOnly cookie 降低 token 被客户端脚本读取的风险；后续 API 调用由服务端统一附加 Authorization。
  cookieStore.set(AUTH_COOKIE_NAME, payload.access_token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });

  redirect("/");
}
