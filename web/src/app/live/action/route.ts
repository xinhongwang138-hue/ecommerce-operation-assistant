import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 直播切片动作代理：定稿 / 重挑。 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { action?: string; clip_id?: number; session_id?: number; status?: string }
    | null;

  const action = body?.action ?? "";
  let path = "";
  let payload: Record<string, unknown> = {};

  if (action === "decide") {
    const clipId = Number(body?.clip_id ?? 0);
    if (!clipId) {
      return NextResponse.json({ error: "缺少 clip_id" }, { status: 400 });
    }
    path = `/api/v1/live/clips/${clipId}/decide`;
    payload = { status: body?.status ?? "" };
  } else if (action === "recut") {
    const sessionId = Number(body?.session_id ?? 0);
    if (!sessionId) {
      return NextResponse.json({ error: "缺少 session_id" }, { status: 400 });
    }
    path = `/api/v1/live/sessions/${sessionId}/recut`;
  } else {
    return NextResponse.json({ error: `未知动作：${action}` }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  const result = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (result?.detail as string) ?? `操作失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(result);
}
