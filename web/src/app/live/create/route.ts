import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 收一场直播的转写。切片不在这里做——后端算完直接返回候选。 */
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const response = await fetch(`${API_BASE_URL}/api/v1/live/sessions`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `提交失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload, { status: response.status });
}
