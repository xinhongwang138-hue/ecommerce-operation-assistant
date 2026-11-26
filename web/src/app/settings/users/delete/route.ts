import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { authHeaders } from "@/lib/session";

/** 处理用户删除请求：调用后端软删除接口，并按结果重定向回用户管理面板。 */
export async function POST(request: Request) {
  const formData = await request.formData();
  const userId = formData.get("user_id")?.toString() ?? "";
  const response = await fetch(`${API_BASE_URL}/api/v1/users/${userId}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });

  // 409 表示该用户已有审计记录、被后端拒绝物理删除，需要提示改用「停用」。
  const code = response.ok ? "deleted" : response.status === 409 ? "delete_blocked" : "delete_failed";
  revalidatePath("/settings");
  redirect(`/settings?user=${code}`);
}
