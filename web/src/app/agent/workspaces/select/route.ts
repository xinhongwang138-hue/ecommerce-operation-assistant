import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { WORKSPACE_COOKIE_NAME } from "@/lib/session";


/** 处理Agent/Workspace/选择请求：读取表单和路径参数，调用后端 API，并按结果响应或重定向。 */
export async function POST(request: Request) {
  const formData = await request.formData();
  const workspaceId = Number(formData.get("workspace_id"));
  if (!Number.isInteger(workspaceId) || workspaceId <= 0) {
    return NextResponse.redirect(new URL("/agent?result=failed", request.url), 303);
  }
  const cookieStore = await cookies();
  cookieStore.set(WORKSPACE_COOKIE_NAME, String(workspaceId), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });
  return NextResponse.redirect(new URL(`/agent?workspace=${workspaceId}`, request.url), 303);
}
