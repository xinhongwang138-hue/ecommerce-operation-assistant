from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission
from app.db.session import get_db
from app.models import User
from app.schemas import ModelSettingsRead, ModelSettingsUpdate
from app.schemas import ModelSettingsTestRequest, ModelSettingsTestResult
from app.services.llm import LLMNotConfiguredError, LLMResponseError, create_json_chat_completion
from app.services.model_settings import get_runtime_settings, read_model_settings, update_model_settings

router = APIRouter(prefix='/model-settings', tags=['model-settings'])
SessionDep = Annotated[Session, Depends(get_db)]
EditorDep = Annotated[User, Depends(require_edit_permission)]


@router.get('', response_model=ModelSettingsRead)
def get_model_settings(db: SessionDep, _current_user: EditorDep) -> dict:
    return read_model_settings(db)


@router.patch('', response_model=ModelSettingsRead)
def patch_model_settings(payload: ModelSettingsUpdate, db: SessionDep, _current_user: EditorDep) -> dict:
    return update_model_settings(payload.model_dump(exclude_unset=True), db)


@router.post('/test', response_model=ModelSettingsTestResult)
def test_model_settings(payload: ModelSettingsTestRequest, _current_user: EditorDep) -> dict:
    if payload.target != 'text':
        raise HTTPException(status_code=501, detail='图片与视频模型测试将在第16章接入。')
    return _test_text_model()


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
