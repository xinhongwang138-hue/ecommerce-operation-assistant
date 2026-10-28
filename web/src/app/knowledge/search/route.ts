import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/**
 * 检索预览代理。
 *
 * 刻意不透传智库的 /stream/{session_id}：那条 SSE 路由按 session_id 取数据、
 * 目前没有租户归属校验，透传给浏览器等于开放"按 ID 读别人会话"。
 * 这里只走非流式 /query，一次请求一次响应，租户由后端签发。
 */
export async function POST(request: Request) {
  const payload = (await request.json().catch(() => null)) as {
    query?: string;
    product_id?: number | null;
    limit?: number;
  } | null;

  const query = (payload?.query ?? "").trim();
  if (!query) {
    return NextResponse.json({ error: "请输入检索内容" }, { status: 400 });
  }
  if (query.length > 500) {
    return NextResponse.json({ error: "检索内容过长（上限 500 字）" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    // 检索含向量化 + 精排，给足超时，不能用默认的 4s
    body: JSON.stringify({
      query,
      product_id: payload?.product_id ?? null,
      limit: Math.min(Math.max(payload?.limit ?? 5, 1), 20),
    }),
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "知识库检索超时，请稍后重试" }, { status: 504 });
  }

  const data = (await response.json().catch(() => null)) as
    | (Record<string, unknown> & { detail?: string })
    | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: data?.detail ?? "知识库检索暂时不可用" },
      { status: response.status },
    );
  }
  return NextResponse.json(data);
}
