import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 图片取字代理：浏览器只访问同源接口，Bearer token 由服务端从 httpOnly Cookie 转发。 */
export async function POST(request: Request) {
  const payload = await request.json();
  const response = await fetch(`${API_BASE_URL}/api/v1/ocr/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(payload),
  });

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    // 把后端的可读原因（未配置/格式不支持/上游失败）透传，前端才能给出准确提示。
    const detail = (data as { detail?: string } | null)?.detail;
    return NextResponse.json({ error: detail ?? "图片取字失败" }, { status: response.status });
  }

  return NextResponse.json(data);
}
