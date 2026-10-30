import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 训练任务动作代理：登记 / 推状态 / 记评测。 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | {
        action?: string;
        dataset_id?: number;
        job_id?: number;
        name?: string;
        base_model?: string;
        status?: string;
        metrics?: Record<string, unknown>;
        note?: string;
        cases?: { question: string; base_score: number; tuned_score: number; note?: string }[];
      }
    | null;

  const action = body?.action ?? "";
  let path = "";
  let payload: Record<string, unknown> = {};

  if (action === "register") {
    path = "/api/v1/finetune/jobs";
    payload = {
      dataset_id: Number(body?.dataset_id ?? 0),
      name: body?.name ?? "",
      base_model: body?.base_model ?? "",
      params: {},
      note: body?.note ?? "",
    };
  } else if (action === "status") {
    path = `/api/v1/finetune/jobs/${Number(body?.job_id ?? 0)}/status`;
    payload = { status: body?.status ?? "", metrics: body?.metrics ?? {}, note: body?.note ?? "" };
  } else if (action === "eval") {
    path = `/api/v1/finetune/jobs/${Number(body?.job_id ?? 0)}/eval`;
    payload = { cases: body?.cases ?? [], note: body?.note ?? "" };
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
  return NextResponse.json(result, { status: response.status });
}
