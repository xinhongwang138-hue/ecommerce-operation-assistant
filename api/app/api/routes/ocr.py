"""图片取字接口：登录用户可把图片转成文字，供标题改写、竞品文案抄录等场景使用。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routes.auth import require_view_permission
from app.core.config import get_settings
from app.models import User
from app.schemas import OcrExtractRequest, OcrExtractResult, OcrStatusRead
from app.services.ocr import (
    OcrNotConfiguredError,
    OcrRequestError,
    OcrResponseError,
    extract_text,
    is_ocr_configured,
)


router = APIRouter(prefix="/ocr", tags=["ocr"])

CurrentUser = Annotated[User, Depends(require_view_permission)]


@router.get("/status", response_model=OcrStatusRead)
def read_status(current_user: CurrentUser) -> OcrStatusRead:
    """暴露脱敏的取字配置，前端据此决定是否提示"未配置模型"。"""
    settings = get_settings()
    return OcrStatusRead(
        configured=is_ocr_configured(settings),
        model=settings.ocr_model,
        base_url=(settings.ocr_base_url or settings.llm_base_url or ""),
        max_image_bytes=settings.ocr_max_image_bytes,
    )


@router.post("/extract", response_model=OcrExtractResult)
def extract(payload: OcrExtractRequest, current_user: CurrentUser) -> OcrExtractResult:
    """识别图片中的文字，返回纯文本与逐行结果。"""
    try:
        result = extract_text(
            image_base64=payload.image_base64,
            mime_type=payload.mime_type,
            prompt=payload.prompt,
        )
    except OcrNotConfiguredError as exc:
        # 未配置属于部署边界，用 400 明确告诉调用方"先配模型"，而不是伪装成服务故障。
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OcrRequestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OcrResponseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return OcrExtractResult(**result)
