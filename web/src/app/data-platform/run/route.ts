import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/**
 * 跑批代理：浏览器不直连后端端口，Bearer token 从 httpOnly Cookie 转发。
 *
 * 用 POST 而不是 GET：跑批会重建整层表，是明确的写操作，不能藏在可被预取、
 * 可被浏览器缓存的 GET 后面。
 */
export async function POST() {
  const response = await fetch(`${API_BASE_URL}/api/v1/data-platform/runs`, {
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
      { error: (payload?.detail as string) ?? `抽取失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload, { status: 200 });
}
