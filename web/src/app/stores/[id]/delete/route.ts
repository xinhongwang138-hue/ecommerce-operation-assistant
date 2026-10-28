import { redirect } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

type RouteContext = {
  params: Promise<{ id: string }>;
};

/** 处理店铺/删除请求：彻底删除一家店铺。后端只放行没有商品的店铺。 */
export async function POST(request: Request, context: RouteContext) {
  const { id } = await context.params;
  const formData = await request.formData();

  // 页面用必填勾选框做二次确认，这里再校验一次，避免绕过表单直接 POST。
  if (formData.get("confirm") !== "yes") {
    redirect(`/stores/${id}?storeError=confirm_required`);
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/stores/${id}`, {
    method: "DELETE",
    headers: {
      ...(await authHeaders()),
    },
  });

  if (response.status === 204) {
    // 店铺列表页在首页工作台，这里不跳回已删除的详情页。
    redirect("/?storeResult=deleted");
  }
  if (response.status === 409) {
    redirect(`/stores/${id}?storeError=has_products`);
  }
  if (response.status === 403) {
    redirect(`/stores/${id}?storeError=forbidden`);
  }
  if (response.status === 404) {
    // 详情页对不存在的店铺直接 notFound()，提示留在原地等于没提示。
    redirect("/?storeResult=notfound");
  }
  redirect(`/stores/${id}?storeError=failed`);
}
