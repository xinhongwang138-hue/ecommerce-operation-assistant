import { NextResponse } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/**
 * 资料列表代理：浏览器只访问同源接口，Bearer token 由服务端从 httpOnly Cookie 转发。
 *
 * 这一层存在的意义就是"浏览器不直连智库端口"：智库本身没有租户概念，
 * 让它直接面向前端等于把跨租户读取的口子开在公网侧。
 */
export async function GET() {
  const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents?limit=100`, {
    headers: await authHeaders(),
    cache: "no-store",
  });

  if (!response.ok) {
    return NextResponse.json(
      { error: `资料列表加载失败（${response.status}）` },
      { status: response.status },
    );
  }
  return NextResponse.json(await response.json());
}

/** 资料上传代理：转发 multipart，后端登记后立刻返回 202，导入在后台跑。 */
export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.includes("multipart/form-data")) {
    return NextResponse.json({ error: "必须以 multipart/form-data 上传文件" }, { status: 415 });
  }

  // 用 formData 而不是裸转发请求体：后者在 Node fetch 下要声明 duplex，
  // 且会把客户端的 Content-Length/边界原样带到上游，多一层不确定性。
  const form = await request.formData().catch(() => null);
  if (!form || !form.get("file")) {
    return NextResponse.json({ error: "请选择要上传的文件" }, { status: 400 });
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents`, {
    method: "POST",
    headers: await authHeaders(),
    body: form,
  }).catch(() => null);

  if (!response) {
    return NextResponse.json({ error: "知识库服务不可达，请稍后重试" }, { status: 502 });
  }

  const data = (await response.json().catch(() => null)) as { detail?: string } | null;
  if (!response.ok) {
    // 透传后端给出的可读原因（类型不支持/超出体积/未配置），前端才能提示准确
    return NextResponse.json(
      { error: data?.detail ?? "上传失败，请稍后重试" },
      { status: response.status },
    );
  }
  return NextResponse.json(data, { status: response.status });
}
