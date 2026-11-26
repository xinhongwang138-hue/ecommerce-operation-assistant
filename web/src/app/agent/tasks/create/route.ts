import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { workspaceAuthHeaders } from "@/lib/session";


/** 处理Agent/任务/创建请求：读取表单和路径参数，调用后端 API，并按结果响应或重定向。 */
export async function POST(request: Request) {
  const formData = await request.formData();
  const workspaceId = positiveInteger(formData.get("workspace_id"));
  if (workspaceId === null) {
    return redirectToAgent(request, null, "failed");
  }
  const productId = positiveInteger(formData.get("product_id"));
  const storeId = positiveInteger(formData.get("store_id"));
  const workflowKey = textValue(formData.get("workflow_key"));
  const intent = textValue(formData.get("intent"));
  const contextScope: Record<string, number> = {};
  if (productId !== null) contextScope.product_id = productId;
  if (storeId !== null) contextScope.store_id = storeId;

  const response = await fetch(`${API_BASE_URL}/api/v1/workspaces/${workspaceId}/agent/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await workspaceAuthHeaders(workspaceId)),
    },
    body: JSON.stringify({
      workflow_key: workflowKey,
      intent,
      context_scope: contextScope,
      input_payload: {},
    }),
  });
  return redirectToAgent(request, workspaceId, response.ok ? "created" : "failed");
}


/** 重定向到Agent，封装当前页面重复使用的转换或展示规则，并统一处理空值边界。 */
function redirectToAgent(request: Request, workspaceId: number | null, result: string) {
  const target = new URL("/agent", request.url);
  if (workspaceId !== null) target.searchParams.set("workspace", String(workspaceId));
  target.searchParams.set("result", result);
  target.hash = "task-center";
  return NextResponse.redirect(target, 303);
}


/** 正整数，封装当前页面重复使用的转换或展示规则，并统一处理空值边界。 */
function positiveInteger(value: FormDataEntryValue | null): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}


/** 文本值，把不可信表单或 JSON 值转换为页面逻辑可安全使用的类型。 */
function textValue(value: FormDataEntryValue | null): string | null {
  const text = typeof value === "string" ? value.trim() : "";
  return text || null;
}
