import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/**
 * 低分样本导出：直接给一个可下载的 JSON。
 *
 * 用附件下载而不是在页面上展开，是因为这个文件的用途是"回流成训练数据/复盘素材"，
 * 用户要的是文件本身。
 */
export async function GET(request: Request) {
  const threshold = new URL(request.url).searchParams.get("threshold") ?? "60";
  if (!/^\d+(\.\d+)?$/.test(threshold)) {
    return NextResponse.json({ error: "threshold 必须是 0-100 的数字" }, { status: 400 });
  }

  const response = await fetch(
    `${API_BASE_URL}/api/v1/qa/export/low-score?threshold=${threshold}`,
    { headers: await authHeaders(), cache: "no-store" },
  ).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  if (!response.ok) {
    return NextResponse.json(
      { error: `导出失败（${response.status}）` },
      { status: response.status },
    );
  }

  const body = await response.text();
  return new NextResponse(body, {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Disposition": `attachment; filename="qa-low-score-${threshold}.json"`,
      "Cache-Control": "no-store",
    },
  });
}
