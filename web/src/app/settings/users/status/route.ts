import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 处理用户启用/停用请求：调用后端更新接口，并按结果重定向回用户管理面板。 */
export async function POST(request: Request) {
  const formData = await request.formData();
  const userId = formData.get("user_id")?.toString() ?? "";
  const nextStatus = formData.get("status")?.toString() === "disabled" ? "disabled" : "active";
  const response = await fetch(`${API_BASE_URL}/api/v1/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ status: nextStatus }),
  });

  revalidatePath("/settings");
  redirect(
    `/settings?user=${
      response.ok ? (nextStatus === "active" ? "restored" : "disabled") : "status_failed"
    }`,
  );
}
