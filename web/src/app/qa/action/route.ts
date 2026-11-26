import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 两个会改数据的动作合成一个入口，前端不用记后端路径。 */
const ACTIONS: Record<string, { path: string; label: string }> = {
  "import-agent": { path: "/api/v1/qa/import/agent-conversations", label: "拉取仓内会话" },
  rescore: { path: "/api/v1/qa/rescore", label: "按当前规则重算" },
};

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as { action?: string } | null;
  const action = body?.action ?? "";
  const target = ACTIONS[action];
  if (!target) {
    return NextResponse.json({ error: `不支持的动作：${action || "(空)"}` }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}${target.path}`, {
    method: "POST",
    headers: await authHeaders(),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `${target.label}失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload);
}
