from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models import ModelProviderSetting, ModelTarget


TARGETS = {
    "text": ModelTarget.TEXT,
    "image": ModelTarget.IMAGE,
    "video": ModelTarget.VIDEO,
}


def read_model_settings(db: Session | None = None, settings: Settings | None = None) -> dict:
    """读取模型配置；集中执行业务校验、状态变更与持久化，并返回可验证结果。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    settings = settings or get_settings()
    rows = _read_rows(db)
    text = _effective_text(settings, rows.get(ModelTarget.TEXT))
    image = _effective_image(settings, rows.get(ModelTarget.IMAGE))
    video = _effective_video(settings, rows.get(ModelTarget.VIDEO))

    # 配置读取接口只返回脱敏 key 和是否已配置，避免前端或浏览器日志暴露供应商密钥。
    return {
        "text": {
            "base_url": text.llm_base_url or "",
            "api_key_masked": _mask_secret(text.llm_api_key),
            "has_api_key": bool(text.llm_api_key),
            "model": text.llm_model or "",
        },
        "image": {
            "provider": image.image_provider or "",
            "base_url": image.image_base_url or "",
            "api_key_masked": _mask_secret(image.image_api_key),
            "has_api_key": bool(image.image_api_key),
            "model": image.image_model or "",
            "size": image.image_size,
            "workspace_id": image.image_workspace_id or "",
            "poll_interval_seconds": image.image_poll_interval_seconds,
            "poll_max_attempts": image.image_poll_max_attempts,
        },
        "video": {
            "provider": video.video_provider or "",
            "base_url": video.video_base_url or "",
            "api_key_masked": _mask_secret(video.video_api_key),
            "has_api_key": bool(video.video_api_key),
            "model": video.video_model or "",
            "size": video.video_size,
            "duration": video.video_duration,
            "workspace_id": video.video_workspace_id or "",
            "poll_interval_seconds": video.video_poll_interval_seconds,
            "poll_max_attempts": video.video_poll_max_attempts,
        },
    }


def update_model_settings(payload: dict, db: Session) -> dict:
    """更新模型配置；集中执行业务校验、状态变更与持久化，并返回可验证结果。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    for target_name, model_target in TARGETS.items():
        target_payload = payload.get(target_name) or {}
        if not target_payload:
            continue
        # 文本、图片、视频供应商分开保存，避免后续只换图片模型时影响文本生成链路。
        row = db.scalars(
            select(ModelProviderSetting).where(ModelProviderSetting.target == model_target)
        ).first()
        if row is None:
            row = ModelProviderSetting(target=model_target)
            db.add(row)

        _apply_payload(row, target_name, target_payload)

    db.commit()
    return read_model_settings(db)


def get_runtime_settings(settings: Settings | None = None) -> Any:
    """获取runtime配置；集中执行业务校验、状态变更与持久化，并返回可验证结果。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    settings = settings or get_settings()
    rows = _read_rows()
    text = _effective_text(settings, rows.get(ModelTarget.TEXT))
    image = _effective_image(settings, rows.get(ModelTarget.IMAGE))
    video = _effective_video(settings, rows.get(ModelTarget.VIDEO))

    # 运行态配置允许数据库覆盖环境变量，便于运营后台调整模型；未配置字段仍回退到部署环境。
    return SimpleNamespace(
        app_name=settings.app_name,
        database_url=settings.database_url,
        api_cors_origins=settings.api_cors_origins,
        llm_base_url=text.llm_base_url,
        llm_api_key=text.llm_api_key,
        llm_model=text.llm_model,
        image_provider=image.image_provider,
        image_base_url=image.image_base_url,
        image_api_key=image.image_api_key,
        image_model=image.image_model,
        image_size=image.image_size,
        image_workspace_id=image.image_workspace_id,
        image_poll_interval_seconds=image.image_poll_interval_seconds,
        image_poll_max_attempts=image.image_poll_max_attempts,
        video_provider=video.video_provider,
        video_base_url=video.video_base_url,
        video_api_key=video.video_api_key,
        video_model=video.video_model,
        video_size=video.video_size,
        video_duration=video.video_duration,
        video_workspace_id=video.video_workspace_id,
        video_poll_interval_seconds=video.video_poll_interval_seconds,
        video_poll_max_attempts=video.video_poll_max_attempts,
    )


def _read_rows(db: Session | None = None) -> dict[ModelTarget, ModelProviderSetting]:
    """读取行，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    if db is not None:
        return _rows_from_session(db)

    try:
        with SessionLocal() as session:
            return _rows_from_session(session)
    except SQLAlchemyError:
        # 应用启动或迁移前可能还没有配置表，回退环境变量，保证健康检查和初始化脚本可运行。
        return {}


def _rows_from_session(db: Session) -> dict[ModelTarget, ModelProviderSetting]:
    """行from会话，统一主流程中的格式、默认值和异常边界。"""
    rows = db.scalars(select(ModelProviderSetting)).all()
    return {row.target: row for row in rows}


def _effective_text(settings: Settings, row: ModelProviderSetting | None) -> SimpleNamespace:
    """生效文本，统一主流程中的格式、默认值和异常边界。"""
    return SimpleNamespace(
        llm_base_url=_row_value(row, "base_url", settings.llm_base_url),
        llm_api_key=_row_value(row, "api_key", settings.llm_api_key),
        llm_model=_row_value(row, "model", settings.llm_model),
    )


def _effective_image(settings: Settings, row: ModelProviderSetting | None) -> SimpleNamespace:
    """生效图片，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    return SimpleNamespace(
        image_provider=_row_value(row, "provider", settings.image_provider),
        image_base_url=_row_value(row, "base_url", settings.image_base_url),
        image_api_key=_row_value(row, "api_key", settings.image_api_key),
        image_model=_row_value(row, "model", settings.image_model),
        image_size=_row_value(row, "size", settings.image_size) or "1024*1024",
        image_workspace_id=_row_value(row, "workspace_id", settings.image_workspace_id),
        image_poll_interval_seconds=_row_value(
            row,
            "poll_interval_seconds",
            settings.image_poll_interval_seconds,
        )
        or 5,
        image_poll_max_attempts=_row_value(
            row,
            "poll_max_attempts",
            settings.image_poll_max_attempts,
        )
        or 24,
    )


def _effective_video(settings: Settings, row: ModelProviderSetting | None) -> SimpleNamespace:
    """生效视频，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    return SimpleNamespace(
        video_provider=_row_value(row, "provider", settings.video_provider),
        video_base_url=_row_value(row, "base_url", settings.video_base_url),
        video_api_key=_row_value(row, "api_key", settings.video_api_key),
        video_model=_row_value(row, "model", settings.video_model),
        video_size=_row_value(row, "size", settings.video_size) or "1280*720",
        video_duration=_row_value(row, "duration", settings.video_duration) or 2,
        video_workspace_id=_row_value(row, "workspace_id", settings.video_workspace_id),
        video_poll_interval_seconds=_row_value(
            row,
            "poll_interval_seconds",
            settings.video_poll_interval_seconds,
        )
        or 10,
        video_poll_max_attempts=_row_value(
            row,
            "poll_max_attempts",
            settings.video_poll_max_attempts,
        )
        or 36,
    )


def _row_value(row: ModelProviderSetting | None, field: str, fallback: Any) -> Any:
    """行值，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    if row is None:
        return fallback
    value = getattr(row, field)
    if value is None or value == "":
        return fallback
    return value


def _apply_payload(row: ModelProviderSetting, target_name: str, payload: dict) -> None:
    """应用载荷，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    if target_name == "text":
        _set_if_present(row, "base_url", payload, "base_url")
        _set_secret_if_present(row, payload)
        _set_if_present(row, "model", payload, "model")
        return

    _set_if_present(row, "provider", payload, "provider")
    _set_if_present(row, "base_url", payload, "base_url")
    _set_secret_if_present(row, payload)
    _set_if_present(row, "model", payload, "model")
    _set_if_present(row, "size", payload, "size")
    _set_if_present(row, "workspace_id", payload, "workspace_id")
    _set_if_present(row, "poll_interval_seconds", payload, "poll_interval_seconds")
    _set_if_present(row, "poll_max_attempts", payload, "poll_max_attempts")
    if target_name == "video":
        _set_if_present(row, "duration", payload, "duration")


def _set_if_present(row: ModelProviderSetting, field: str, payload: dict, payload_field: str) -> None:
    """设置ifpresent，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    if payload_field not in payload:
        return
    value = payload[payload_field]
    if isinstance(value, str):
        value = value.strip() or None
    setattr(row, field, value)


def _set_secret_if_present(row: ModelProviderSetting, payload: dict) -> None:
    """设置密钥ifpresent，统一主流程中的格式、默认值和异常边界。"""
    # 按“输入归一化 → 业务校验 → 状态或持久化”推进，异常不得留下半完成结果。
    if "api_key" not in payload:
        return
    value = payload["api_key"]
    if isinstance(value, str):
        value = value.strip()
    # 空字符串表示“不更新密钥”，防止保存表单时把已有供应商 key 意外清空。
    if value:
        row.api_key = value


def _mask_secret(value: str | None) -> str:
    """脱敏密钥：只保留末 4 位供人工辨认"配的是哪把"，前缀一律不回传。

    旧实现对 >16 字符的密钥保留前 7 + 后 6，等于把 13 个明文字符交给读取方；
    配置表是全局的、GET 又只对 edit 权限开放，这 13 位会被所有租户的 operator 持有。
    前端判断"是否已配置"走 has_api_key，不依赖这里的字符量。
    """
    if not value:
        return ""
    if len(value) <= 8:
        return "******"
    return f"******{value[-4:]}"
