import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 入库代理：POST 提交文本，DELETE 按 `?id=` 删除。 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { title?: string; content?: string; source_kind?: "text" | "markdown" }
    | null;
  const title = (body?.title ?? "").trim();
  const content = (body?.content ?? "").trim();
  if (!title || !content) {
    return NextResponse.json({ error: "标题与正文都不能为空" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/rag/documents`, {
    method: "POST",
    headers: { ...(await authHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ title, content, source_kind: body?.source_kind ?? "text" }),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }

  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `入库失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(payload, { status: 201 });
}

export async function DELETE(request: Request) {
  const id = new URL(request.url).searchParams.get("id");
  if (!id || !/^\d+$/.test(id)) {
    return NextResponse.json({ error: "缺少合法的文档 id" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/rag/documents/${id}`, {
    method: "DELETE",
    headers: await authHeaders(),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  if (!response.ok) {
    return NextResponse.json({ error: `删除失败（${response.status}）` }, { status: response.status });
  }
  return new NextResponse(null, { status: 204 });
}
