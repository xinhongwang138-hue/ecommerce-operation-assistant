import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 合同动作代理：语义复核 / 审批留痕。
 *
 * 合成一个入口是因为两者共用同一套错误映射（400 模型未配、422 决策不合法或
 * 高风险没写理由、404 不存在），分成两个文件只是把同一段代码抄两遍。
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | { action?: string; id?: number; decision?: string; comment?: string }
    | null;

  const action = body?.action ?? "";
  const id = Number(body?.id ?? 0);
  if (!id) {
    return NextResponse.json({ error: "缺少合同 id" }, { status: 400 });
  }

  let path = "";
  let payload: Record<string, unknown> = {};
  if (action === "review") {
    path = `/api/v1/contracts/${id}/review`;
  } else if (action === "approve") {
    path = `/api/v1/contracts/${id}/approvals`;
    payload = { decision: body?.decision ?? "", comment: body?.comment ?? "" };
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
