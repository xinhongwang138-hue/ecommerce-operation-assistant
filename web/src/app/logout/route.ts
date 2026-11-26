import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { AUTH_COOKIE_NAME, WORKSPACE_COOKIE_NAME, getAuthToken } from "@/lib/session";

/** 处理退出登录请求：先让后端吊销当前令牌，再清除本地 Cookie。 */
export async function POST() {
  const token = await getAuthToken();
  if (token) {
    try {
      // 服务端令牌撤销：登出后即使 token 被复制也无法继续使用。
      await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
    } catch {
      // 后端暂不可用时不阻塞登出，本地 Cookie 仍然会被清除。
    }
  }
  const cookieStore = await cookies();
  cookieStore.delete(AUTH_COOKIE_NAME);
  cookieStore.delete(WORKSPACE_COOKIE_NAME);
  redirect("/login");
}
