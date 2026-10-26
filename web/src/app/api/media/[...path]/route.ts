import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/**
 * 媒体同源代理：浏览器只访问本路由，Bearer 由服务端从 httpOnly Cookie 附加。
 * 后端 /uploads 已改为登录后受控读取，这样 <img>/<a> 标签也能正常拿到图片和视频。
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const encoded = path.map((segment) => encodeURIComponent(segment)).join("/");
  const response = await fetch(`${API_BASE_URL}/uploads/${encoded}`, {
    headers: await authHeaders(),
    cache: "no-store",
  });

  if (!response.ok) {
    return new Response(null, { status: response.status });
  }

  const headers = new Headers();
  const contentType = response.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set("cache-control", "private, max-age=300");
  headers.set("x-content-type-options", "nosniff");
  return new Response(await response.arrayBuffer(), { status: 200, headers });
}
