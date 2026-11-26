"""受控的上传文件读取接口。

生成图片/视频放在 api/uploads 下，文件名虽为 uuid，但仍要求登录后才能读取，
避免工作区素材被匿名抓取。前端通过 Next 的同源代理访问（见 web/src/app/api/media）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.routes.auth import require_view_permission
from app.core.config import API_DIR
from app.models import User

router = APIRouter(prefix="/uploads", tags=["uploads"])

UPLOAD_DIR = (API_DIR / "uploads").resolve()


@router.get("/{file_path:path}")
def read_upload(
    file_path: str,
    _current_user: Annotated[User, Depends(require_view_permission)],
) -> FileResponse:
    """返回上传目录内的文件；严格限制在 uploads 根目录内，阻断路径穿越。"""
    target = (UPLOAD_DIR / file_path).resolve()
    try:
        target.relative_to(UPLOAD_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(
        target,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )
