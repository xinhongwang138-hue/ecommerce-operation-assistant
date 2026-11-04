import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 合同上传代理。
 *
 * 转发 multipart 不能自己序列化 body：边界串（boundary）在 Content-Type 里，
 * 手拼一个就会出现"后端收到的文件是空的"。直接把原 FormData 转出去。
 */
export async function POST(request: Request) {
  const incoming = await request.formData().catch(() => null);
  const file = incoming?.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ error: "请选择一个文件" }, { status: 400 });
  }

  const form = new FormData();
  form.append("file", file, file.name);

  const response = await fetch(`${API_BASE_URL}/api/v1/contracts/upload`, {
    method: "POST",
    headers: await authHeaders(),
    body: form,
    cache: "no-store",
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "后端不可达，请确认 API 已启动" }, { status: 502 });
  }
  const payload = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) {
    // 415/422 的 detail 是给人看的原话，原样透出去，不要再包一层"上传失败"
    return NextResponse.json(
      { error: (payload?.detail as string) ?? `上传失败（${response.status}）` },
      { status: response.status },
    );
  }
  // 透传 201：上传是创建，前端要能用状态码区分"新建"和"已存在但提示重复"
  return NextResponse.json(payload, { status: response.status });
}
