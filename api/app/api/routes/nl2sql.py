"""NL2SQL 问数（P03）接口。

权限用 edit 而不是 view：问数是只读的，但每一次提问都会真实调用模型、计入当日
配额。放开给 viewer 等于让只读账号消耗全租户的模型预算——只读权限管的是"能不
能看数"，管不了"能不能花钱"，所以这里按后者划线。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, workspace_scope
from app.db.session import get_db
from app.models import User
from app.nl2sql import service
from app.nl2sql.errors import NL2SQLRejected, WarehouseNotReady
from app.nl2sql.schemas import AskRead, AskRequest, ExampleRead
from app.services.llm import LLMNotConfiguredError, LLMResponseError

router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])

SessionDep = Annotated[Session, Depends(get_db)]
EditUserDep = Annotated[User, Depends(require_edit_permission)]


@router.get("/examples", response_model=list[ExampleRead])
def read_examples(_user: EditUserDep) -> list[ExampleRead]:
    """示例问题：覆盖 ADS / DWS / DWD 三个下探层级。"""
    return service.examples()


@router.post("/ask", response_model=AskRead)
def ask(payload: AskRequest, db: SessionDep, _user: EditUserDep) -> AskRead:
    """自然语言转 SQL 并执行。

    模型给出的 SQL 与用户在查询框手敲的 SQL 走同一个护栏——这是刻意的：模型是
    不可信输入源，不能因为"是我们自己调的模型"就给它开一条捷径。
    """
    try:
        return service.ask(workspace_scope(db), payload.question)
    except WarehouseNotReady as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据仓尚未构建，请先在数据中台执行一次抽取",
        ) from exc
    except NL2SQLRejected as exc:
        # 422 而不是 500：问题出在"这次问法生成不出安全 SQL"，用户换问法即可解决，
        # 把每次被拒的原因一并带回去，否则用户只能看到一句没有信息量的失败。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "attempts": exc.attempts},
        ) from exc
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
