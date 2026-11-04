"""自建 RAG（P04）接口。

权限：入库/删除走 edit，检索与浏览走 view——知识库的主要使用者是"查资料的人"，
而一次检索只花一次小 embedding 调用（不是对话模型），把 view 挡在外面等于让只读
账号无法使用知识库，那这个功能就没有意义。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.db.session import get_db
from app.models import User
from app.rag import service
from app.rag.schemas import (
    DocumentRead,
    IngestRead,
    IngestRequest,
    SearchRead,
    SearchRequest,
    StatsRead,
)

router = APIRouter(prefix="/rag", tags=["rag"])

SessionDep = Annotated[Session, Depends(get_db)]
ViewUserDep = Annotated[User, Depends(require_view_permission)]
EditUserDep = Annotated[User, Depends(require_edit_permission)]


def _workspace(db: Session) -> int:
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="知识库检索必须绑定 Workspace",
        )
    return int(workspace_id)


@router.get("/stats", response_model=StatsRead)
def read_stats(db: SessionDep, _user: ViewUserDep) -> StatsRead:
    """文档数 / 切片数 / 向量覆盖率。覆盖率是"这个库现在靠不靠得住"的唯一指标。"""
    return service.stats(db, _workspace(db))


@router.get("/documents", response_model=list[DocumentRead])
def read_documents(db: SessionDep, _user: ViewUserDep) -> list[DocumentRead]:
    return service.list_documents(db, _workspace(db))


@router.post("/documents", response_model=IngestRead, status_code=status.HTTP_201_CREATED)
def create_document(payload: IngestRequest, db: SessionDep, user: EditUserDep) -> IngestRead:
    """入库一篇文本：切片 + 尽量向量化。

    向量化失败仍返回 201：文档已经在库里，只是本次没有向量，`warnings` 会写明原因。
    把"降级"报成"失败"会让用户以为要重传，其实不用。
    """
    try:
        return service.ingest(
            db,
            _workspace(db),
            title=payload.title,
            content=payload.content,
            source_kind=payload.source_kind,
            created_by_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, db: SessionDep, _user: EditUserDep) -> Response:
    """跨租户删除一律 404，不区分"不存在"与"不属于你"。"""
    if not service.delete_document(db, _workspace(db), document_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchRead)
def search(payload: SearchRequest, db: SessionDep, _user: ViewUserDep) -> SearchRead:
    """混合检索：BM25 与向量各给一份名次，RRF 融合后返回 top_k。

    每条命中都带 `char_start` / `char_end`：引用要能落到原文位置，否则用户没法核对。
    """
    return service.search(db, _workspace(db), payload.query, top_k=payload.top_k)
