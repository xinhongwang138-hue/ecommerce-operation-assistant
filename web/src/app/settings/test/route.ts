import { redirect } from "next/navigation";
import { NextRequest } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 处理模型设置/test请求：读取表单和路径参数，调用后端 API，并按结果响应或重定向。 */
export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const target = String(formData.get("target") ?? "text");

  // 连通性测试只请求后端测试接口，不在前端创建素材记录或保存供应商响应。
  const response = await fetch(`${API_BASE_URL}/api/v1/model-settings/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ target }),
  });

  let message = "";
  try {
    const data = await response.json();
    message = String(data.message ?? data.detail ?? "");
  } catch {
    // 供应商或代理返回非 JSON 时仍给用户一个可读状态，避免设置页空白失败。
    message = response.ok ? "娴嬭瘯閫氳繃" : "娴嬭瘯澶辫触";
  }

  const params = new URLSearchParams({
    test: target,
    status: response.ok ? "ok" : "error",
    message,
  });

  redirect(`/settings?${params.toString()}`);
}


