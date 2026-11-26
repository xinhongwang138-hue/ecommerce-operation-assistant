import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 数据集导出代理。
 *
 * 直接把后端的 JSONL 流和 Content-Disposition 一起透传，不在服务端落地文件：
 * 数据集动辄几十兆，写进磁盘等于留一份没人清理的副本。
 */
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const datasetId = Number(id);
  if (!datasetId) {
    return NextResponse.json({ error: "datasetId 不合法" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/finetune/datasets/${datasetId}/export`, {
    headers: await authHeaders(),
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    return NextResponse.json(
      { error: payload?.detail ?? `导出失败（${response.status}）` },
      { status: response.status },
    );
  }

  const body = await response.text();
  return new NextResponse(body, {
    status: 200,
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Content-Disposition":
        response.headers.get("content-disposition") ?? `attachment; filename="dataset-${datasetId}.jsonl"`,
    },
  });
}
