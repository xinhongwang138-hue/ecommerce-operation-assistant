"""知识库资料服务：落盘、分发导入、回写进度。

原文必须由电商侧留存：智库的 MinIO 只存抽取出的图片，原始文档只落在其
output/YYYYMMDD/{task_id}/ 临时目录，随时可能被清理；没有这一份，"引用出处指向
具体文档"就无从落地。

进度回传沿用本仓库既有做法（DB 状态 + 前端轮询），不复用 AgentRunEvent：
那是 Agent 运行的事件流，语义不同，混用会让两边的面板互相污染。
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import API_DIR, get_settings
from app.models import KnowledgeDocument, KnowledgeDocumentStatus
from app.services.knowledge_proxy import (
    KnowledgeNotConfiguredError,
    KnowledgeUpstreamError,
    fetch_import_status,
    search as kb_search,
    upload_document,
)

logger = logging.getLogger(__name__)

UPLOAD_DIR = (API_DIR / "uploads").resolve()
KNOWLEDGE_SUBDIR = "knowledge"

# 与智库 /upload 的主文档白名单保持一致（图片只能作为附属文件随文档上传）。
# 刻意不含 .svg：可携带脚本，而智库曾长期把对象桶设为匿名可读。
ALLOWED_SUFFIXES = {".pdf", ".md", ".markdown"}

# 智库导入的等待上限：MinerU 侧单文件轮询超时默认就是 1800s，
# 低于它会把还在正常解析的大 PDF 误判成失败。
IMPORT_DEADLINE_SECONDS = 2400
POLL_INTERVAL_SECONDS = 3

# 智库节点名 → 电商侧阶段。按 done_list 出现的最深阶段推进，
# 这样前端进度条在智库节点重排时也不会倒退。
STAGE_BY_NODE: tuple[tuple[tuple[str, ...], KnowledgeDocumentStatus], ...] = (
    (("node_pdf_to_md", "node_md_img"), KnowledgeDocumentStatus.PARSING),
    (("node_document_split",), KnowledgeDocumentStatus.SPLITTING),
    (("node_item_name_recognition",), KnowledgeDocumentStatus.RECOGNIZING),
    (("node_bge_embedding",), KnowledgeDocumentStatus.EMBEDDING),
    (("node_import_milvus",), KnowledgeDocumentStatus.IMPORTING),
)


def knowledge_dir(workspace_id: int) -> Path:
    """按租户分目录存放原文，物理上就区分开，便于排查串号问题"""
    return UPLOAD_DIR / KNOWLEDGE_SUBDIR / str(int(workspace_id))


def store_upload(workspace_id: int, upload: UploadFile) -> tuple[str, str, Path, int]:
    """
    校验并落盘一份上传，返回 (安全文件名, 相对存储路径, 绝对路径, 字节数)

    :param workspace_id: 当前租户
    :param upload: 上传对象
    :return: 展示名、相对路径、落盘路径、大小
    :raises HTTPException: 类型不支持或超出体积上限
    """
    settings = get_settings()
    # 只取 basename：文件名可能带 ../ 或绝对路径，直接拼接会写到 uploads 之外
    original_name = Path(upload.filename or "unnamed").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型 {suffix or '(无后缀)'}，仅支持 {sorted(ALLOWED_SUFFIXES)}",
        )

    target_dir = knowledge_dir(workspace_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = target_dir / stored_name

    limit = settings.knowledge_max_upload_bytes
    written = 0
    with target.open("wb") as handle:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"文件超过 {limit // (1024 * 1024)}MB 上限",
                )
            handle.write(chunk)
    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件内容为空")

    relative = f"{KNOWLEDGE_SUBDIR}/{workspace_id}/{stored_name}"
    display_name = Path(original_name).stem
    return display_name, relative, target, written


def create_document(
    db: Session,
    *,
    workspace_id: int,
    upload: UploadFile,
    user_id: int | None,
    product_id: int | None,
) -> KnowledgeDocument:
    """
    登记一份待导入资料

    :param db: 当前会话
    :param workspace_id: 已校验成员关系的租户
    :param upload: 上传对象
    :param user_id: 上传者
    :param product_id: 可选归属商品
    :return: 已落库、状态为 pending 的记录
    """
    display_name, relative, target, size = store_upload(workspace_id, upload)
    document = KnowledgeDocument(
        workspace_id=workspace_id,
        product_id=product_id,
        uploaded_by_id=user_id,
        title=display_name[:255],
        original_filename=Path(upload.filename or display_name).name[:255],
        stored_path=relative,
        content_type=upload.content_type,
        size_bytes=size,
        status=KnowledgeDocumentStatus.PENDING,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    logger.info(
        "知识库资料已登记 id=%s workspace=%s path=%s size=%s",
        document.id, workspace_id, relative, size,
    )
    return document


def dispatch_import(document_id: int) -> None:
    """
    把导入交给 Celery；未启用 Celery 时退回后台线程，保持本地 inline 开发可用

    :param document_id: knowledge_documents.id
    """
    settings = get_settings()
    if settings.task_queue_backend == "celery":
        from app.worker.tasks import submit_knowledge_import

        submit_knowledge_import.delay(document_id)
        return

    import threading

    threading.Thread(
        target=_run_import_isolated,
        args=(document_id,),
        name=f"kb-import-{document_id}",
        daemon=True,
    ).start()


def _run_import_isolated(document_id: int) -> None:
    """后台线程模式下自开一个会话，不能复用请求的 Session"""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        try:
            import_document(db, document_id)
        except Exception:  # noqa: BLE001  后台线程兜底，异常已落库
            logger.exception("知识库导入线程失败 document=%s", document_id)


def import_document(db: Session, document_id: int) -> KnowledgeDocument:
    """
    提交智库并轮询到终态，逐阶段回写状态

    :param db: 独立会话（worker 或后台线程）
    :param document_id: knowledge_documents.id
    :return: 更新后的记录
    """
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise KnowledgeUpstreamError(f"资料不存在：{document_id}")

    file_path = (UPLOAD_DIR / document.stored_path).resolve()
    try:
        file_path.relative_to(UPLOAD_DIR)
    except ValueError as exc:
        _mark_failed(db, document, "资料存储路径异常")
        raise KnowledgeUpstreamError("资料存储路径异常") from exc

    try:
        task_ids = upload_document(document.workspace_id, file_path, document.original_filename)
    except (KnowledgeNotConfiguredError, KnowledgeUpstreamError) as exc:
        _mark_failed(db, document, str(exc))
        raise

    document.kb_task_id = task_ids[0]
    document.status = KnowledgeDocumentStatus.SUBMITTING
    document.submitted_at = datetime.now(UTC)
    document.status_message = "已提交知识库，等待解析"
    db.commit()
    db.refresh(document)

    return poll_until_done(db, document)


def poll_until_done(db: Session, document: KnowledgeDocument) -> KnowledgeDocument:
    """
    轮询智库进度并推进本地状态

    :param db: 会话
    :param document: 已有 kb_task_id 的记录
    :return: 更新后的记录
    """
    deadline = datetime.now(UTC).timestamp() + IMPORT_DEADLINE_SECONDS
    while datetime.now(UTC).timestamp() < deadline:
        try:
            remote = fetch_import_status(document.workspace_id, document.kb_task_id or "")
        except KnowledgeUpstreamError as exc:
            logger.warning("智库进度查询失败 document=%s err=%s", document.id, exc)
            time_sleep(POLL_INTERVAL_SECONDS)
            continue

        raw_status = str(remote.get("status") or "")
        done_list = [str(n) for n in (remote.get("done_list") or [])]
        failed_list = [str(n) for n in (remote.get("failed_list") or [])]

        if raw_status == "completed":
            _mark_ready(db, document, done_list)
            return document
        if raw_status == "failed":
            _mark_failed(
                db, document,
                f"知识库导入失败于：{', '.join(failed_list) or '未知节点'}",
            )
            return document

        document.status = _stage_of(done_list)
        document.status_message = f"已完成：{', '.join(done_list) or '（等待开始）'}"
        db.commit()
        db.refresh(document)
        time_sleep(POLL_INTERVAL_SECONDS)

    _mark_failed(db, document, f"知识库导入超过 {IMPORT_DEADLINE_SECONDS // 60} 分钟未完成")
    return document


def _stage_of(done_list: list[str]) -> KnowledgeDocumentStatus:
    """按已完成节点推断当前阶段，取最深的一档"""
    stage = KnowledgeDocumentStatus.SUBMITTING
    for nodes, candidate in STAGE_BY_NODE:
        if any(node in done_list for node in nodes):
            stage = candidate
    return stage


def _mark_ready(db: Session, document: KnowledgeDocument, done_list: list[str]) -> None:
    document.status = KnowledgeDocumentStatus.READY
    document.status_message = "入库完成，可被检索与引用"
    document.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(document)
    logger.info("知识库资料入库完成 document=%s workspace=%s", document.id, document.workspace_id)


def _mark_failed(db: Session, document: KnowledgeDocument, message: str) -> None:
    document.status = KnowledgeDocumentStatus.FAILED
    document.error_message = message[:2000]
    document.status_message = "导入失败"
    document.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(document)
    logger.warning("知识库资料导入失败 document=%s reason=%s", document.id, message)


def time_sleep(seconds: int) -> None:
    """单独包一层，方便测试时替换成假时钟"""
    import time

    time.sleep(seconds)


def agent_knowledge_refs(
    db: Session,
    workspace_id: int,
    query: str,
    *,
    limit: int = 4,
) -> list[dict]:
    """为 Agent 取回本租户可用的资料片段。

    任何失败都只记日志并返回空列表：知识库是增强项，它挂了不能把整条商品文案
    生成链路一起拖停，也不能让 Agent 拿到没有归属的裸文本。

    :param db: 会话
    :param workspace_id: 租户
    :param query: 检索问题（一般由商品名 + 类目拼出）
    :param limit: 最多取几条
    :return: [{file_title, chunk_id, parent_title, snippet, score, document_id}]
    """
    ready = db.scalar(
        select(func.count(KnowledgeDocument.id)).where(
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.status == KnowledgeDocumentStatus.READY,
        )
    )
    if not ready:
        # 这个租户压根没有已入库资料：不打智库，省掉一次必然空手的往返
        return []

    try:
        result = kb_search(workspace_id, query[:500])
    except (KnowledgeNotConfiguredError, KnowledgeUpstreamError) as exc:
        logger.warning("Agent 知识库检索跳过 workspace=%s reason=%s", workspace_id, exc)
        return []

    # 智库在"没认出商品主体"时会直接回一句消歧追问，sources 为空但 HTTP 是 200。
    # 不区分这一种空，Agent 就会长期静默地拿不到资料而无人知情。
    done_list = [str(node) for node in (result.get("done_list") or [])]
    search_ran = any(node.startswith("node_search_embedding") for node in done_list)
    if not search_ran:
        logger.warning(
            "知识库未执行检索（多半是没认出商品主体，智库回了消歧追问）"
            " workspace=%s item_names=%s done_list=%s",
            workspace_id, result.get("item_names"), done_list,
        )

    refs: list[dict] = []
    for source in (result.get("sources") or [])[:limit]:
        if not isinstance(source, dict):
            continue
        file_title = (source.get("file_title") or "").strip()
        snippet = (source.get("snippet") or "").strip()
        if not file_title or not snippet:
            continue
        # 反查本租户资料行：只有能对上号的出处才允许展示成"可点击的来源"，
        # 对不上的说明不是本租户登记的文档，宁可不给链接
        document_id = db.scalar(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.workspace_id == workspace_id,
                KnowledgeDocument.title == file_title[:255],
            )
        )
        refs.append(
            {
                "file_title": file_title[:255],
                "chunk_id": str(source.get("chunk_id") or "") or None,
                "parent_title": (source.get("parent_title") or source.get("title") or "")[:255] or None,
                "snippet": snippet[:400],
                "score": source.get("score") if isinstance(source.get("score"), (int, float)) else None,
                "document_id": int(document_id) if document_id is not None else None,
            }
        )
    logger.info("Agent 知识库取回 %s 条出处 workspace=%s", len(refs), workspace_id)
    return refs
