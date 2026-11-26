import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/**
 * 问数代理：浏览器不直连后端端口，Bearer token 从 httpOnly Cookie 转发。
 *
 * 后端 422 的 detail 是对象（`{message, attempts}`）而不是字符串——每次被拒的原因
 * 要原样透给前端，否则用户只看到"生成失败"，没有任何可调整的方向。
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as { question?: string } | null;
  const question = (body?.question ?? "").trim();
  if (!question) {
    return NextResponse.json({ error: "问题不能为空" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/nl2sql/ask`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }

  const payload = (await response.json().catch(() => null)) as
    | { detail?: unknown }
    | Record<string, unknown>
    | null;

  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const typed = detail as { message: string; attempts?: string[] };
      return NextResponse.json(
        { error: typed.message, attempts: typed.attempts ?? [] },
        { status: response.status },
      );
    }
    return NextResponse.json(
      { error: typeof detail === "string" ? detail : `问数失败（${response.status}）` },
      { status: response.status },
    );
  }

  return NextResponse.json(payload, { status: 200 });
}
