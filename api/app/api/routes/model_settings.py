import ipaddress
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_owner_permission, require_view_permission
from app.db.session import get_db
from app.models import User
from app.schemas import ModelSettingsRead, ModelSettingsUpdate, ModelUsageRead
from app.schemas import ModelSettingsTestRequest, ModelSettingsTestResult
from app.services.link_extraction import _is_blocked_address
from app.services.llm import LLMNotConfiguredError, LLMResponseError, create_json_chat_completion
from app.services.model_settings import get_runtime_settings, read_model_settings, read_model_usage, update_model_settings

router = APIRouter(prefix='/model-settings', tags=['model-settings'])
SessionDep = Annotated[Session, Depends(get_db)]
EditorDep = Annotated[User, Depends(require_edit_permission)]
# 模型配置是全平台共享的，写操作必须收口到全局 owner，避免任一租户的 operator 影响其他租户。
OwnerDep = Annotated[User, Depends(require_owner_permission)]
# token 用量是只读展示，任意登录用户（含 viewer）均可查看。
ViewerDep = Annotated[User, Depends(require_view_permission)]


@router.get('', response_model=ModelSettingsRead)
def get_model_settings(db: SessionDep, _current_user: EditorDep) -> dict:
    return read_model_settings(db)


@router.get('/usage', response_model=ModelUsageRead)
def get_model_usage(db: SessionDep, _current_user: ViewerDep) -> dict:
    """累计 token 用量（只读聚合），供模型配置页展示。"""
    return read_model_usage(db)


@router.patch('', response_model=ModelSettingsRead)
def patch_model_settings(payload: ModelSettingsUpdate, db: SessionDep, _current_user: OwnerDep) -> dict:
    _validate_base_urls(payload.model_dump(exclude_unset=True))
    return update_model_settings(payload.model_dump(exclude_unset=True), db)


@router.post('/test', response_model=ModelSettingsTestResult)
def test_model_settings(payload: ModelSettingsTestRequest, _current_user: OwnerDep) -> dict:
    if payload.target != 'text':
        raise HTTPException(status_code=501, detail='图片与视频模型测试将在第16章接入。')
    return _test_text_model()


def _validate_base_urls(payload: dict) -> None:
    """只接受 http/https，且禁止把 base_url 指向本机/内网字面量，避免把 /test 变成 SSRF。"""
    for target_payload in payload.values():
        if not isinstance(target_payload, dict):
            continue
        base_url = target_payload.get('base_url')
        if not base_url:
            continue
        parsed = urlparse(str(base_url))
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise HTTPException(status_code=422, detail='base_url must be a valid http(s) URL')
        host = parsed.hostname.lower()
        if host in {'localhost', '127.0.0.1', '::1'}:
            raise HTTPException(status_code=422, detail='base_url must not point to a local address')
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if _is_blocked_address(ip):
            raise HTTPException(status_code=422, detail='base_url must not point to a private address')


def _test_text_model() -> dict:
    settings = get_runtime_settings()
    try:
        result, _raw = create_json_chat_completion(
            [
                {'role': 'system', 'content': '你是一个接口连通性测试助手，只返回 JSON。'},
                {'role': 'user', 'content': '返回 {"ok": true, "message": "ready"}'},
            ],
            max_tokens=80,
            temperature=0,
            settings=settings,
        )
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if result.get('ok') is not True:
        raise HTTPException(status_code=502, detail='Text model test response must contain ok=true.')
    return {'target': 'text', 'ok': True, 'message': 'Text model is reachable and returned ok=true.'}
