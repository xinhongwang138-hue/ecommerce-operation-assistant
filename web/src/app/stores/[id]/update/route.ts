import { redirect } from "next/navigation";
import { z } from "zod";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 店铺状态：closed 即系统原本的"软删除"语义，关店后数据全部保留。 */
const storeStatusSchema = z.enum(["active", "paused", "closed"]);

type RouteContext = {
  params: Promise<{ id: string }>;
};

/** 处理店铺/更新请求：改店铺状态（启用/暂停/关闭），再回详情页并带上结果标记。 */
export async function POST(request: Request, context: RouteContext) {
  const { id } = await context.params;
  const formData = await request.formData();
  const parsed = storeStatusSchema.safeParse(formData.get("status"));
  if (!parsed.success) {
    redirect(`/stores/${id}?storeError=invalid`);
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/stores/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
    },
    body: JSON.stringify({ status: parsed.data }),
  });

  if (response.status === 403) {
    redirect(`/stores/${id}?storeError=forbidden`);
  }
  if (response.status === 404) {
    // 详情页对不存在的店铺直接 notFound()，提示只能落在首页。
    redirect("/?storeResult=notfound");
  }
  if (!response.ok) {
    redirect(`/stores/${id}?storeError=failed`);
  }

  redirect(`/stores/${id}?storeStatus=${parsed.data}`);
}
