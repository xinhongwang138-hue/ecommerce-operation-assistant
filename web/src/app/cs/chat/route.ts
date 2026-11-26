import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 客服对话代理：一次问答（会写会话，必要时建工单）。 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { message?: string; conversation_id?: number | null }
    | null;
  const message = (body?.message ?? "").trim();
  if (!message) {
    return NextResponse.json({ error: "说点什么再发" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/cs/chat`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: body?.conversation_id ?? null }),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `发送失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload);
}
