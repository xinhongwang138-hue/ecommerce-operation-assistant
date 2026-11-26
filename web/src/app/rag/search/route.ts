import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 检索代理：浏览器不直连后端端口，Bearer token 从 httpOnly Cookie 转发。 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { query?: string; top_k?: number }
    | null;
  const query = (body?.query ?? "").trim();
  if (!query) {
    return NextResponse.json({ error: "检索词不能为空" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/rag/search`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: body?.top_k ?? 6 }),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }

  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `检索失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload, { status: 200 });
}
