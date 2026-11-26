/**
 * 后端不可达时，Node/Next 的 fetch 没有默认超时，页面会一直停留在加载态。
 * 这里提供带 AbortController 硬超时的统一封装：超时立即中止请求，让上层快速进入错误态或未登录态。
 */

/** 默认超时时间（毫秒），可通过 NEXT_PUBLIC_API_TIMEOUT_MS 覆盖，默认 4 秒。 */
export const API_TIMEOUT_MS = resolveTimeout(process.env.NEXT_PUBLIC_API_TIMEOUT_MS, 4000);

function resolveTimeout(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * 发起带硬超时的请求。
 * 若调用方自带 signal，会与内部超时 signal 联动，任一触发都会中止请求。
 */
export async function fetchWithTimeout(
  input: string | URL,
  init: RequestInit = {},
  timeoutMs: number = API_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const upstream = init.signal;
  const abortFromUpstream = () => controller.abort();

  if (upstream) {
    if (upstream.aborted) {
      controller.abort();
    } else {
      upstream.addEventListener("abort", abortFromUpstream, { once: true });
    }
  }

  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
    upstream?.removeEventListener("abort", abortFromUpstream);
  }
}

/** 判断错误是否由超时中止引起，便于上层区分"超时"与"后端返回错误"。 */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError");
}
