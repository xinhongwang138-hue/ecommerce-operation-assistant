"""掌柜智库 HTTP 代理层。

浏览器只访问电商后端，智库端口（8010/8011）不对前端暴露。本模块是唯一能
到达智库的出接口，负责：
  1. 用已校验过成员关系的 workspace 签发内部令牌，租户只进请求头，不进请求体；
  2. 把上游异常收敛成类型化错误，路由层再映射成通用提示——上游原文里可能带
     内网地址与栈信息，不能原样吐给客户端（SECURITY_AUDIT 已点过一次）。

两个容易踩的实现细节，都写死在这里：
  · trust_env=False：本机环境里 HTTP_PROXY/HTTPS_PROXY 是有值的，httpx 默认会
    读它们，导致对 127.0.0.1 的内网调用被代理劫走（表现为 502，排查时极易误判成
    智库挂了）。web/scripts/start_web_3002.ps1 用 NO_PROXY 治过同类问题。
  · 地址只来自后端 .env，不提供任何"按租户配置智库地址"的入口，否则就重现
    SECURITY_AUDIT V4/V5 的 SSRF 面。
"""
import logging
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.knowledge_auth import mint_internal_token

logger = logging.getLogger(__name__)


class KnowledgeNotConfiguredError(RuntimeError):
    """知识库未配置或未启用：调用方返回 503，绝不退化成匿名请求"""


class KnowledgeUpstreamError(RuntimeError):
    """智库返回非 2xx 或不可达：detail 只进日志，不进响应"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _require_endpoints() -> tuple[str, str, int]:
    """读取智库地址与超时；缺配置直接关闭功能"""
    settings = get_settings()
    import_base = (settings.kb_import_base_url or "").strip().rstrip("/")
    query_base = (settings.kb_query_base_url or "").strip().rstrip("/")
    if not import_base or not query_base or not (settings.kb_internal_secret or "").strip():
        raise KnowledgeNotConfiguredError("知识库未配置（KB_IMPORT_BASE_URL / KB_QUERY_BASE_URL / KB_INTERNAL_SECRET）")
    return import_base, query_base, int(settings.kb_timeout_seconds)


def _request(
    method: str,
    url: str,
    *,
    workspace_id: int,
    timeout: float,
    **kwargs: Any,
) -> Any:
    """
    统一发出带内部令牌的请求

    :param method: HTTP 方法
    :param url: 完整 URL
    :param workspace_id: 当前请求绑定的工作区（已校验成员关系）
    :param timeout: 单次请求超时秒数
    :param kwargs: 透传给 httpx 的 json/files/data 等
    :return: 上游返回的 JSON
    :raises KnowledgeUpstreamError: 不可达或非 2xx
    """
    headers = dict(kwargs.pop("headers", None) or {})
    headers["X-KB-Internal"] = mint_internal_token(workspace_id)

    # trust_env=False：不让系统代理拦截回环地址上的内部调用
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        # 记录原始异常，对外只给通用描述
        logger.warning("智库请求失败 url=%s workspace=%s err=%s", url, workspace_id, exc)
        raise KnowledgeUpstreamError("知识库服务暂时不可用") from exc

    if response.status_code >= 400:
        logger.warning(
            "智库返回错误 url=%s workspace=%s status=%s body=%s",
            url, workspace_id, response.status_code, response.text[:500],
        )
        raise KnowledgeUpstreamError(
            "知识库请求被拒绝", status_code=response.status_code
        )
    try:
        return response.json()
    except ValueError as exc:
        raise KnowledgeUpstreamError("知识库返回体不是合法 JSON") from exc


def upload_document(workspace_id: int, file_path: Path, display_name: str) -> list[str]:
    """
    把一份文档交给智库导入流水线

    :param workspace_id: 租户
    :param file_path: 已落盘的原始文件路径（原文由电商侧留存，智库不存原始文档）
    :param display_name: 传给智库的文件名，决定 Milvus 里的 file_title
    :return: 智库任务 ID 列表（一份主文档一个）
    """
    import_base, _, timeout = _require_endpoints()
    if not file_path.exists():
        raise KnowledgeUpstreamError("待导入文件已不存在")

    with file_path.open("rb") as handle:
        payload = handle.read()

    result = _request(
        "POST",
        f"{import_base}/upload",
        workspace_id=workspace_id,
        timeout=timeout,
        files={"files": (display_name, payload, "application/octet-stream")},
    )
    task_ids = [str(t) for t in (result.get("task_ids") or [])]
    if not task_ids:
        accepted = result.get("details") or []
        reason = accepted[0].get("reason") if accepted and isinstance(accepted[0], dict) else "无"
        raise KnowledgeUpstreamError(f"知识库未受理该文件：{reason}")
    return task_ids


def fetch_import_status(workspace_id: int, task_id: str) -> dict[str, Any]:
    """
    查询一次导入进度

    调用方必须先用 knowledge_documents 校验 task_id 属于该 workspace，
    本函数不接受"任意 task_id"的透传语义。

    :param workspace_id: 租户
    :param task_id: 智库任务 ID
    :return: {status, done_list, running_list, failed_list}
    """
    import_base, _, timeout = _require_endpoints()
    result = _request(
        "GET",
        f"{import_base}/status/{task_id}",
        workspace_id=workspace_id,
        timeout=min(timeout, 30),
    )
    return result if isinstance(result, dict) else {}


def search(workspace_id: int, query: str, session_id: str | None = None) -> dict[str, Any]:
    """
    走非流式检索，供工作台预览与 Agent 工具使用

    刻意不使用 /stream/{session_id}：那条 SSE 路由按 session_id 取数据，
    目前没有租户归属校验，透传给前端等于开放按 ID 读取他人会话。

    :param workspace_id: 租户
    :param query: 检索问题
    :param session_id: 可选会话 ID，缺省由智库生成
    :return: {answer, sources, item_names, session_id}
    """
    _, query_base, timeout = _require_endpoints()
    body: dict[str, Any] = {"query": query, "is_stream": False}
    if session_id:
        body["session_id"] = session_id
    result = _request(
        "POST",
        f"{query_base}/query",
        workspace_id=workspace_id,
        timeout=max(timeout, 120),  # 检索含向量化+精排，比上传慢
        json=body,
    )
    return {
        "answer": result.get("answer") or "",
        "sources": result.get("sources") or [],
        "item_names": result.get("item_names") or [],
        "session_id": result.get("session_id") or "",
    }
