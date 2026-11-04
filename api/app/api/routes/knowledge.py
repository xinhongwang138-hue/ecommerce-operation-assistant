"""掌柜智库工作台接口。

安全约定（本模块的每一条都对应一条硬约束）：
  · 租户 id 只从已校验成员关系的会话里取（workspace_scope 缺省即 403），
    绝不接受请求体/查询参数里的 workspace_id；
  · 每个查询都带 workspace_id 条件，跨租户命中一律按 404 处理，
    不区分"不存在"和"不属于你"，避免用状态码探测他租户的资料是否存在；
  · 智库 task_id / session_id 只从本库映射取用，不提供任何"按上游 ID 直查"的入口。
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.db.session import get_db
from app.models import KnowledgeDocument, KnowledgeDocumentStatus, User
from app.schemas import (
    KnowledgeDocumentRead,
    KnowledgeSearchRead,
    KnowledgeSearchRequest,
    KnowledgeSourceRead,
)
from app.services.knowledge_documents import create_document, dispatch_import
from app.services.knowledge_proxy import (
    KnowledgeNotConfiguredError,
    KnowledgeUpstreamError,
    search as kb_search,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

SessionDep = Annotated[Session, Depends(get_db)]


def _require_workspace(db: Session) -> int:
    """取当前租户；没有绑定就拒绝（智库侧租户是强制条件，这里不能放行）"""
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace context is required for knowledge base access",
        )
    return int(workspace_id)


def _get_owned_document(db: Session, document_id: int, workspace_id: int) -> KnowledgeDocument:
    """按 (id, workspace_id) 取资料：别人的资料一律 404"""
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.workspace_id == workspace_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")
    return document


@router.get("/documents", response_model=list[KnowledgeDocumentRead])
def list_documents(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    document_status: Annotated[KnowledgeDocumentStatus | None, Query(alias="status")] = None,
) -> list[KnowledgeDocument]:
    """列出本租户的资料，可按状态过滤"""
    workspace_id = _require_workspace(db)
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.workspace_id == workspace_id)
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(limit)
    )
    if document_status is not None:
        stmt = stmt.where(KnowledgeDocument.status == document_status)
    return list(db.scalars(stmt).all())


@router.post("/documents", response_model=KnowledgeDocumentRead, status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
    file: Annotated[UploadFile, File()],
    product_id: Annotated[int | None, Form()] = None,
) -> KnowledgeDocument:
    """收下文件、登记资料并把导入交给后台，立刻返回 202

    不在请求线程里等智库：一份 PDF 的解析+向量化是分钟级，浏览器超时后用户会
    以为失败而重复上传。
    """
    workspace_id = _require_workspace(db)
    if file.filename is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少文件名")

    document = create_document(
        db,
        workspace_id=workspace_id,
        upload=file,
        user_id=current_user.id,
        product_id=product_id,
    )
    dispatch_import(document.id)
    return document


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentRead)
def get_document(
    document_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> KnowledgeDocument:
    """查单份资料进度：只认本租户的记录，前端轮询这个接口看阶段推进"""
    workspace_id = _require_workspace(db)
    return _get_owned_document(db, document_id, workspace_id)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> None:
    """删除本租户的资料记录与留存原文

    注意：智库侧的切片暂不删除——它没有对外删除接口，Milvus 只能按 doc_id 清。
    因此删过的资料在检索里可能仍出现，界面上要如实提示这一点，不能假装已彻底清除。
    """
    workspace_id = _require_workspace(db)
    document = _get_owned_document(db, document_id, workspace_id)

    from app.services.knowledge_documents import UPLOAD_DIR

    stored = (UPLOAD_DIR / document.stored_path).resolve()
    try:
        stored.relative_to(UPLOAD_DIR)
        if stored.is_file():
            stored.unlink()
    except ValueError:
        # 路径异常时只删库记录，不做任何文件系统操作
        pass

    db.delete(document)
    db.commit()


@router.post("/search", response_model=KnowledgeSearchRead)
def search_knowledge(
    payload: KnowledgeSearchRequest,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> KnowledgeSearchRead:
    """检索预览：把问题转给智库，出处原样带回

    商品范围目前只用于提示语（智库按 item_name 过滤，不按 product_id）。
    真正的隔离由租户条件保证，这里不再叠加"按商品 ID 直查"的口子。
    """
    workspace_id = _require_workspace(db)

    query = payload.query.strip()
    if payload.product_id is not None:
        owned = db.scalar(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.product_id == payload.product_id,
                KnowledgeDocument.workspace_id == workspace_id,
            )
        )
        if owned is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="该商品在本租户下没有关联资料",
            )

    try:
        result = kb_search(workspace_id, query)
    except KnowledgeNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except KnowledgeUpstreamError as exc:
        # 上游原文只进日志，不给客户端（可能含内网地址与栈信息）
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="知识库检索暂时不可用，请稍后重试",
        ) from exc

    sources = []
    for item in result["sources"][: payload.limit]:
        if not isinstance(item, dict):
            continue
        sources.append(
            KnowledgeSourceRead(
                id=item.get("id"),
                title=_text(item.get("title")),
                parent_title=_text(item.get("parent_title")),
                file_title=_text(item.get("file_title")),
                chunk_id=_text(item.get("chunk_id")),
                score=item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                snippet=_text(item.get("snippet")),
                source=_text(item.get("source")),
            )
        )
    return KnowledgeSearchRead(
        answer=result["answer"],
        sources=sources,
        item_names=[_text(n) or "" for n in result["item_names"]],
    )


def _text(value: object) -> str | None:
    """把上游字段收敛成短字符串，避免异常类型或超长文本直接进入响应"""
    if value is None:
        return None
    text = str(value)
    return text[:1000] if text else None
