import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 建数据集代理。 */
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const response = await fetch(`${API_BASE_URL}/api/v1/finetune/datasets`, {
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
    // 422 的 detail 会直接说明"没有可用样本、该去哪个模块产生数据"，原样透出去
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `构建失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload, { status: response.status });
}
