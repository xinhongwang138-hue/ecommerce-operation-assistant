import { API_BASE_URL } from "@/lib/api";
import { workspaceAuthHeaders } from "@/lib/session";

/** 处理 WebSocket 票据转发请求：用登录态换取 60 秒一次性票据，浏览器再带票据连后端 WS。 */
export async function POST(request: Request) {
  let workspaceId: number | null = null;
  try {
    const payload = (await request.json()) as { workspace_id?: number };
    const parsed = Number(payload.workspace_id);
    workspaceId = Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  } catch {
    workspaceId = null;
  }
  if (workspaceId === null) {
    return Response.json({ detail: "workspace_id is required" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/chat/ws-ticket`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await workspaceAuthHeaders(workspaceId)),
    },
  });
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
