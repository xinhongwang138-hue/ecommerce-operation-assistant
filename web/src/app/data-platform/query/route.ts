import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

const MAX_SQL_LENGTH = 4000;

/** 只读查询代理：把 SQL 原样送到后端护栏，错误信息按 detail 透出给用户。 */
export async function POST(request: Request) {
  const payload = (await request.json().catch(() => null)) as { sql?: unknown } | null;
  const sql = typeof payload?.sql === "string" ? payload.sql : "";
  if (!sql.trim()) {
    return NextResponse.json({ error: "请输入要执行的 SQL" }, { status: 400 });
  }
  if (sql.length > MAX_SQL_LENGTH) {
    return NextResponse.json({ error: `SQL 过长（上限 ${MAX_SQL_LENGTH} 字符）` }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/data-platform/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ sql }),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }

  const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    return NextResponse.json(
      { error: (body?.detail as string) ?? `查询失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(body, { status: 200 });
}
