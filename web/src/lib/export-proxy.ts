import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 代理下载后端 CSV：token 只在服务端附加，浏览器拿不到明文；返回附件流。 */
export async function proxyCsvDownload(apiPath: string, fallbackName: string): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}${apiPath}`, {
    headers: await authHeaders(),
    cache: "no-store",
  });

  if (!response.ok) {
    return new Response("导出失败：请确认已登录且后端服务可用。", {
      status: response.status,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  const headers = new Headers();
  headers.set("Content-Type", "text/csv; charset=utf-8");
  headers.set(
    "Content-Disposition",
    response.headers.get("Content-Disposition") ?? `attachment; filename="${fallbackName}"`,
  );
  return new Response(await response.arrayBuffer(), { headers });
}
