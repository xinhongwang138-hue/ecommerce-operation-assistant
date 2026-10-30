import type {
  AdExperimentDashboard,
  AlertSummary,
  AgentRun,
  AgentApproval,
  AgentCatalog,
  AgentMemory,
  AgentTask,
  AuthToken,
  DataPlatformLineage,
  DataPlatformMetric,
  DataPlatformMetricSeries,
  CsFaqItem,
  CsSummary,
  CsTicket,
  Contract,
  ContractScoringRow,
  ContractSummary,
  FinetuneDataset,
  FinetuneJob,
  FinetuneSummary,
  LiveSession,
  LiveSessionListItem,
  LiveSummary,
  DataPlatformOverview,
  DataPlatformQualityReport,
  ModelSettings,
  ModelUsage,
  Nl2SqlExample,
  QaLeaderboard,
  QaSessions,
  QaSummary,
  RagDocument,
  RagStats,
  Product,
  ProductDetail,
  Store,
  KnowledgeDocument,
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

/** 请求知识库资料列表；失败返回空数组，与其余列表读取保持一致的降级方式。 */
export async function getKnowledgeDocuments(): Promise<KnowledgeDocument[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/knowledge/documents?limit=100`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as KnowledgeDocument[];
  } catch {
    return [];
  }
}

/** 数据中台概览：未跑批时返回 ready=false 的空结构，页面据此提示"先抽一次"。 */
export async function getDataPlatformOverview(): Promise<DataPlatformOverview | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/data-platform/overview`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as DataPlatformOverview;
  } catch {
    return null;
  }
}

/** 指标口径目录：与仓是否就绪无关，口径声明在代码里。 */
export async function getDataPlatformMetrics(): Promise<DataPlatformMetric[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/data-platform/metrics`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as DataPlatformMetric[];
  } catch {
    return [];
  }
}

/** 最近一次跑批的质量校验结果。 */
export async function getDataPlatformQuality(): Promise<DataPlatformQualityReport | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/data-platform/quality`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as DataPlatformQualityReport;
  } catch {
    return null;
  }
}

/** 表级血缘：节点带层，边带建表步骤名。 */
export async function getDataPlatformLineage(): Promise<DataPlatformLineage | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/data-platform/lineage`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as DataPlatformLineage;
  } catch {
    return null;
  }
}

/** 单指标时间序列：右端取仓内最新业务日期，days 由后端夹在 1..90。 */
export async function getDataPlatformMetricSeries(
  key: string,
  days = 14,
): Promise<DataPlatformMetricSeries | null> {
  try {
    const response = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/data-platform/metrics/${encodeURIComponent(key)}?days=${days}`,
      { headers: await authHeaders(), cache: "no-store" },
    );
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as DataPlatformMetricSeries;
  } catch {
    return null;
  }
}

/** 问数示例：各自指向不同层级的表，用来教用户"什么时候该下探到明细"。 */
export async function getNl2SqlExamples(): Promise<Nl2SqlExample[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/nl2sql/examples`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as Nl2SqlExample[];
  } catch {
    return [];
  }
}

/** 自建 RAG：文档清单与覆盖率。检索走客户端代理，因为它是按需触发的。 */
export async function getRagDocuments(): Promise<RagDocument[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/rag/documents`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as RagDocument[];
  } catch {
    return [];
  }
}

export async function getRagStats(): Promise<RagStats | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/rag/stats`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as RagStats;
  } catch {
    return null;
  }
}

export async function getQaSummary(days = 30): Promise<QaSummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/qa/summary?days=${days}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as QaSummary;
  } catch {
    return null;
  }
}

export async function getQaLeaderboard(days = 30): Promise<QaLeaderboard | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/qa/leaderboard?days=${days}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as QaLeaderboard;
  } catch {
    return null;
  }
}

export async function getQaSessions(limit = 20, days?: number): Promise<QaSessions | null> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (days) {
    query.set("days", String(days));
  }
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/qa/sessions?${query}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as QaSessions;
  } catch {
    return null;
  }
}

export async function getCsSummary(): Promise<CsSummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/cs/summary`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as CsSummary;
  } catch {
    return null;
  }
}

export async function getCsTickets(limit = 10): Promise<CsTicket[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/cs/tickets?limit=${limit}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as CsTicket[];
  } catch {
    return [];
  }
}

export async function getCsFaq(): Promise<CsFaqItem[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/cs/faq`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as CsFaqItem[];
  } catch {
    return [];
  }
}

export async function getContractSummary(): Promise<ContractSummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/contracts/summary`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as ContractSummary;
  } catch {
    return null;
  }
}

export async function getContracts(limit = 20): Promise<Contract[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/contracts?limit=${limit}`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as Contract[];
  } catch {
    return [];
  }
}

export async function getContractScoring(): Promise<ContractScoringRow[]> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/contracts/scoring`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as ContractScoringRow[];
  } catch {
    return [];
  }
}

export async function getLiveSummary(): Promise<LiveSummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/live/summary`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as LiveSummary;
  } catch {
    return null;
  }
}

export async function getLiveSessions(limit = 10): Promise<LiveSession[]> {
  try {
    const listResponse = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/live/sessions?limit=${limit}`,
      { headers: await authHeaders(), cache: "no-store" },
    );
    if (!listResponse.ok) {
      return [];
    }
    const items = (await listResponse.json()) as LiveSessionListItem[];
    if (items.length === 0) {
      return [];
    }
    // 列表不带切片，逐场取详情。场次是十几个量级，这里不做批量接口。
    const details = await Promise.all(
      items.map(async (item) => {
        const response = await fetchWithTimeout(
          `${API_BASE_URL}/api/v1/live/sessions/${item.id}`,
          { headers: await authHeaders(), cache: "no-store" },
        );
        return response.ok ? ((await response.json()) as LiveSession) : null;
      }),
    );
    return details.filter((item): item is LiveSession => item !== null);
  } catch {
    return [];
  }
}

export async function getFinetuneSummary(): Promise<FinetuneSummary | null> {
  try {
    const response = await fetchWithTimeout(`${API_BASE_URL}/api/v1/finetune/summary`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as FinetuneSummary;
  } catch {
    return null;
  }
}

export async function getFinetuneDatasets(limit = 20): Promise<FinetuneDataset[]> {
  try {
    const response = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/finetune/datasets?limit=${limit}`,
      { headers: await authHeaders(), cache: "no-store" },
    );
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as FinetuneDataset[];
  } catch {
    return [];
  }
}

export async function getFinetuneJobs(limit = 20): Promise<FinetuneJob[]> {
  try {
    const response = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/finetune/jobs?limit=${limit}`,
      { headers: await authHeaders(), cache: "no-store" },
    );
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as FinetuneJob[];
  } catch {
    return [];
  }
}
