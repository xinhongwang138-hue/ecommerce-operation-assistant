import type {
  AdExperimentDashboard,
  AlertSummary,
  AgentRun,
  AgentApproval,
  AgentCatalog,
  AgentMemory,
  AgentTask,
  AuthToken,
  ModelSettings,
  ModelUsage,
  Product,
  ProductDetail,
  Store,
  StoreInventoryMovementRow,
  StoreInventorySummary,
  UserAccount,
  WorkspaceAccess,
} from "@/types";
import { API_TIMEOUT_MS, fetchWithTimeout } from "./fetch-timeout";
import { authHeaders, workspaceAuthHeaders } from "./session";

const API_BASE_URL =
  process.env.API_INTERNAL_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://127.0.0.1:8000";

// 后端列表接口默认分页 limit=20；不显式翻页会让第 21 条之后的数据静默丢失。
const LIST_PAGE_SIZE = 100;
const LIST_MAX_PAGES = 100;

/** 分页拉取全量列表，避免后端默认 limit 造成页面看不到完整数据。 */
async function fetchAllPages<T>(path: string, headers: Record<string, string>): Promise<T[]> {
  const items: T[] = [];
  for (let page = 0; page < LIST_MAX_PAGES; page += 1) {
    const offset = page * LIST_PAGE_SIZE;
    const response = await fetchWithTimeout(
      `${API_BASE_URL}${path}?offset=${offset}&limit=${LIST_PAGE_SIZE}`,
      { headers, cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error(`Failed to load ${path}: ${response.status}`);
    }
    const batch = (await response.json()) as T[];
    items.push(...batch);
    if (batch.length < LIST_PAGE_SIZE) {
      return items;
    }
  }
  // 安全上限（100 × 100 = 10000 条）内返回已取数据，避免无限翻页拖垮服务端渲染。
  return items;
}

/** 请求后端获取商品接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getProducts(): Promise<Product[]> {
  try {
    // 工作台服务端渲染依赖实时运营数据，禁用缓存，避免导入或生成后仍看到旧状态。
    return await fetchAllPages<Product>("/api/v1/products", await authHeaders());
  } catch {
    return [];
  }
}

/** 请求后端获取stores接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getStores(): Promise<Store[]> {
  try {
    return await fetchAllPages<Store>("/api/v1/stores", await authHeaders());
  } catch {
    return [];
  }
}

/** 请求后端获取店铺接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getStore(id: string): Promise<Store | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/stores/${id}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取店铺库存汇总接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getStoreInventorySummary(id: string): Promise<StoreInventorySummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/stores/${id}/inventory-summary`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取店铺库存movements接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getStoreInventoryMovements(id: string): Promise<StoreInventoryMovementRow[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/stores/${id}/inventory-movements`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return [];
    }

    return response.json();
  } catch {
    return [];
  }
}

/** 请求后端获取商品接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getProduct(id: string): Promise<ProductDetail | null> {
  try {
    // 商品详情页一次读取完整单品闭环数据，后续 tab 只在页面内做展示切换。
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/products/${id}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取投放实验看板接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAdExperimentDashboard(id: string): Promise<AdExperimentDashboard | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/products/${id}/ad-experiments/dashboard`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取Agent运行接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAgentRuns(id: string): Promise<AgentRun[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/products/${id}/agent-runs`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return [];
    }

    return response.json();
  } catch {
    return [];
  }
}

/** 请求后端获取Workspace接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getWorkspaces(): Promise<WorkspaceAccess[]> {
  return fetchAgentResource<WorkspaceAccess[]>("/api/v1/workspaces", await authHeaders());
}

/** 请求后端获取Agentcatalog接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAgentCatalog(workspaceId: number): Promise<AgentCatalog | null> {
  return fetchAgentResource<AgentCatalog | null>(
    `/api/v1/workspaces/${workspaceId}/agent/catalog`,
    await workspaceAuthHeaders(workspaceId),
  );
}

/** 请求后端获取Agent任务接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAgentTasks(workspaceId: number): Promise<AgentTask[]> {
  return fetchAgentResource<AgentTask[]>(
    `/api/v1/workspaces/${workspaceId}/agent/tasks`,
    await workspaceAuthHeaders(workspaceId),
  );
}

/** 请求后端获取Agent审批接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAgentApprovals(workspaceId: number): Promise<AgentApproval[]> {
  return fetchAgentResource<AgentApproval[]>(
    `/api/v1/workspaces/${workspaceId}/agent/approvals`,
    await workspaceAuthHeaders(workspaceId),
  );
}

/** 请求后端获取Agent记忆接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAgentMemories(workspaceId: number): Promise<AgentMemory[]> {
  return fetchAgentResource<AgentMemory[]>(
    `/api/v1/workspaces/${workspaceId}/agent/memories`,
    await workspaceAuthHeaders(workspaceId),
  );
}

/** 请求后端获取Workspace商品接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getWorkspaceProducts(workspaceId: number): Promise<Product[]> {
  try {
    return await fetchAllPages<Product>("/api/v1/products", await workspaceAuthHeaders(workspaceId));
  } catch {
    throw new AgentApiError("Agent API is unavailable", null);
  }
}

/** 请求后端获取Workspacestores接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getWorkspaceStores(workspaceId: number): Promise<Store[]> {
  try {
    return await fetchAllPages<Store>("/api/v1/stores", await workspaceAuthHeaders(workspaceId));
  } catch {
    throw new AgentApiError("Agent API is unavailable", null);
  }
}

/** 请求后端获取model模型设置接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getModelSettings(): Promise<ModelSettings | null> {
  try {
    // 模型配置接口只返回脱敏 key，页面可展示配置状态但拿不到明文供应商密钥。
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/model-settings`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/**
 * 诊断模型配置读取失败原因，供 UI 层展示准确提示文案。
 * 返回 "unauthorized"（401/403，未登录或登录过期）、"network"（网络错误/后端不可达）或 null（读取成功）。
 * 仅在 getModelSettings 返回 null 时由页面调用，不影响原接口签名与调用参数。
 */
export async function getModelSettingsError(): Promise<"unauthorized" | "network" | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/model-settings`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (response.ok) {
      return null;
    }
    if (response.status === 401 || response.status === 403) {
      return "unauthorized";
    }
    return "network";
  } catch {
    return "network";
  }
}

/** 请求后端获取累计 token 用量接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getModelUsage(): Promise<ModelUsage | null> {
  try {
    // 累计用量从 agent_runs.usage 聚合，只读展示，禁用缓存避免看到过期数据。
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/model-settings/usage`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取待办中心汇总，统一处理认证、Workspace、错误和强类型响应。 */
export async function getAlertSummary(): Promise<AlertSummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/alerts/summary`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取users接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getUsers(): Promise<UserAccount[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/users`, {
      headers: await authHeaders(),
      cache: "no-store",
    });

    if (!response.ok) {
      return [];
    }

    return response.json();
  } catch {
    return [];
  }
}

/** 请求后端登录接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function login(email: string, password: string): Promise<AuthToken | null> {
  try {
    // 登录失败统一返回 null，让页面只展示通用错误，避免泄露账号是否存在。
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/** 请求后端获取当前用户接口，统一处理认证、Workspace、错误和强类型响应。 */
export async function getCurrentUser(token: string): Promise<UserAccount | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });

    if (!response.ok) {
      return null;
    }

    return response.json();
  } catch {
    return null;
  }
}

/**
 * 轻量探测后端是否可达，供页面在"数据为空"时区分"真的没有数据"和"后端不可用"。
 * 只读 /health，不触发任何业务逻辑，超时 3 秒。
 */
export async function isBackendAvailable(): Promise<boolean> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/health`, { cache: "no-store" }, 3000);
    return response.ok;
  } catch {
    return false;
  }
}

export { API_BASE_URL, API_TIMEOUT_MS, fetchWithTimeout };

export class AgentApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
  }
}

/** 请求后端请求Agentresource接口，统一处理认证、Workspace、错误和强类型响应。 */
async function fetchAgentResource<T>(
  path: string,
  headers: Record<string, string>,
): Promise<T> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}${path}`, { headers, cache: "no-store" });
    if (!response.ok) {
      throw new AgentApiError(`Agent API request failed: ${response.status}`, response.status);
    }
    return response.json();
  } catch (error) {
    if (error instanceof AgentApiError) throw error;
    throw new AgentApiError("Agent API is unavailable", null);
  }
}
