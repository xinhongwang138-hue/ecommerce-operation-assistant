import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 处理数字人凭证转发请求：由服务端附加登录态获取后端凭证，避免前端直接接触密钥接口。 */
export async function GET() {
  const response = await fetch(`${API_BASE_URL}/api/v1/digital-human/credentials`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
