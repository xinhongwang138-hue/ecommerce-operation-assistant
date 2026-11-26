import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { workspaceAuthHeaders } from "@/lib/session";


type RouteContext = { params: Promise<{ approvalId: string }> };


/** 处理Agent/审批/决策请求：读取表单和路径参数，调用后端 API，并按结果响应或重定向。 */
export async function POST(request: Request, context: RouteContext) {
  const { approvalId } = await context.params;
  const formData = await request.formData();
  const workspaceId = Number(formData.get("workspace_id"));
  const decision = formData.get("decision");
  const editedOutputText = String(formData.get("edited_output") ?? "").trim();
  const valid =
    Number.isInteger(workspaceId) &&
    workspaceId > 0 &&
    /^\d+$/.test(approvalId) &&
    (decision === "approve" || decision === "reject");
  let succeeded = false;
  let decisionPayload: Record<string, unknown> = {};
  if (valid && decision === "approve" && editedOutputText) {
    try {
      const editedOutput = JSON.parse(editedOutputText);
      if (!editedOutput || typeof editedOutput !== "object" || Array.isArray(editedOutput)) {
        throw new Error("edited_output must be an object");
      }
      decisionPayload = { edited_output: editedOutput };
    } catch {
      return redirectToAgent(request, workspaceId, false);
    }
  }
  if (valid) {
    const response = await fetch(
      `${API_BASE_URL}/api/v1/workspaces/${workspaceId}/agent/approvals/${approvalId}/decision`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(await workspaceAuthHeaders(workspaceId)),
        },
        body: JSON.stringify({ decision, decision_payload: decisionPayload }),
      },
    );
    succeeded = response.ok;
  }
  return redirectToAgent(request, workspaceId, succeeded);
}


/** 重定向到Agent，封装当前页面重复使用的转换或展示规则，并统一处理空值边界。 */
function redirectToAgent(request: Request, workspaceId: number, succeeded: boolean) {
  const target = new URL("/agent", request.url);
  if (Number.isInteger(workspaceId) && workspaceId > 0) {
    target.searchParams.set("workspace", String(workspaceId));
  }
  target.searchParams.set("result", succeeded ? "approved" : "failed");
  target.hash = "approval-inbox";
  return NextResponse.redirect(target, 303);
}
