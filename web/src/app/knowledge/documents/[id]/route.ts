import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

type Params = { params: Promise<{ id: string }> };

/** 单份资料进度代理：前端轮询它看阶段推进。id 归属由后端按租户校验，这里不判定。 */
export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  const response = await fetch(
    `${API_BASE_URL}/api/v1/knowledge/documents/${encodeURIComponent(id)}`,
    { headers: await authHeaders(), cache: "no-store" },
  );

  const data = (await response.json().catch(() => null)) as { detail?: string } | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: data?.detail ?? "资料状态查询失败" },
      { status: response.status },
    );
  }
  return NextResponse.json(data);
}

/** 删除代理。智库侧切片暂不删除，提示文案由前端如实说明。 */
export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  const response = await fetch(
    `${API_BASE_URL}/api/v1/knowledge/documents/${encodeURIComponent(id)}`,
    { method: "DELETE", headers: await authHeaders() },
  );

  if (!response.ok) {
    const data = (await response.json().catch(() => null)) as { detail?: string } | null;
    return NextResponse.json(
      { error: data?.detail ?? "删除失败" },
      { status: response.status },
    );
  }
  return NextResponse.json({ ok: true });
}
