import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

type RouteContext = {
  params: Promise<{ runId: string }>;
};

/** 处理Agent运行SSE流式读取：转发认证与 Workspace 上下文，并透传后端流式响应。 */
export async function GET(_request: Request, context: RouteContext) {
  const { runId } = await context.params;
  const upstream = await fetch(`${API_BASE_URL}/api/v1/agent-runs/${runId}/stream`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") ?? "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
