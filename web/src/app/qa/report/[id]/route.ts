import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 单会话报告代理：GET /qa/report/<id> → 后端 /qa/sessions/{id}。 */
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^\d+$/.test(id)) {
    return NextResponse.json({ error: "缺少合法的会话 id" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/qa/sessions/${id}`, {
    headers: await authHeaders(),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `读取报告失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload);
}
