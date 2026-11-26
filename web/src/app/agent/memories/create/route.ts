import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { workspaceAuthHeaders } from "@/lib/session";


/** 处理Agent/记忆/创建请求：读取表单和路径参数，调用后端 API，并按结果响应或重定向。 */
export async function POST(request: Request) {
  const formData = await request.formData();
  const workspaceId = Number(formData.get("workspace_id"));
  const scopeType = String(formData.get("scope_type") ?? "");
  const scopeId = String(formData.get("scope_id") ?? "").trim() || null;
  const memoryKey = String(formData.get("memory_key") ?? "").trim();
  const memoryValue = String(formData.get("memory_value") ?? "").trim();
  const valid =
    Number.isInteger(workspaceId) &&
    workspaceId > 0 &&
    ["workspace", "store", "product", "user"].includes(scopeType) &&
    memoryKey.length > 0 &&
    memoryValue.length > 0;
  let succeeded = false;
  if (valid) {
    const response = await fetch(
      `${API_BASE_URL}/api/v1/workspaces/${workspaceId}/agent/memories`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(await workspaceAuthHeaders(workspaceId)),
        },
        body: JSON.stringify({
          scope_type: scopeType,
          scope_id: scopeId,
          memory_key: memoryKey,
          schema_version: "1.0",
          content: { value: memoryValue },
          source_type: "user_confirmed",
          source_reference: { source: "agent_center" },
        }),
      },
    );
    succeeded = response.ok;
  }
  const target = new URL("/agent", request.url);
  if (Number.isInteger(workspaceId) && workspaceId > 0) {
    target.searchParams.set("workspace", String(workspaceId));
  }
  target.searchParams.set("result", succeeded ? "memory-created" : "failed");
  target.hash = "memory";
  return NextResponse.redirect(target, 303);
}
