"""图片取字（OCR）：把用户上传的图片交给视觉模型，直接返回可复制的纯文本。

为什么单列一个服务，而不是塞进 llm.py：业务文本生成要求模型返回 JSON 对象，
而取字要的是原始文本，两者的输出契约、模型选型和失败边界都不同，混在一起会互相污染。
"""

import base64
import binascii
import re
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.services.spend_quota import charge_llm_call


# 只放行浏览器能直接预览、且 DashScope 视觉接口稳定支持的格式，避免把任意二进制塞给模型。
ALLOWED_MIME_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/bmp": "bmp",
}

# qwen-vl-ocr 在没有约束时会返回 "x,y,w,h,angle" 坐标行（这是它的原生输出格式），
# 实测只有明确禁止坐标，才会给出可直接使用的纯文本。
DEFAULT_PROMPT = "识别图中所有文字，只输出文字内容，不要输出任何坐标、位置或角度信息。"

_COORDINATE_LINE = re.compile(r"^\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*-?\d+\s*$")
_DATA_URL_PREFIX = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


class OcrNotConfiguredError(RuntimeError):
    """未配置 OCR 模型凭据。"""


class OcrRequestError(RuntimeError):
    """入参不合法（格式、体积、编码）。"""


class OcrResponseError(RuntimeError):
    """供应商返回异常或内容无法解析。"""


def is_ocr_configured(settings: Settings | None = None) -> bool:
    """OCR 是否可用；未单独配置 OCR_BASE_URL/OCR_API_KEY 时回退到文本模型凭据。"""
    settings = settings or get_settings()
    return bool(_base_url(settings) and _api_key(settings) and (settings.ocr_model or "").strip())


def extract_text(
    *,
    image_base64: str,
    mime_type: str,
    prompt: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """识别图片中的文字，返回纯文本与按行拆分的结果。"""
    settings = settings or get_settings()
    if not is_ocr_configured(settings):
        raise OcrNotConfiguredError("OCR provider is not configured.")

    mime = (mime_type or "").strip().lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise OcrRequestError(
            "Unsupported image type. Allowed: " + ", ".join(sorted(ALLOWED_MIME_TYPES))
        )

    raw = _decode_image(image_base64, settings)
    if len(raw) > settings.ocr_max_image_bytes:
        limit_mb = settings.ocr_max_image_bytes / 1024 / 1024
        raise OcrRequestError(
            f"Image is too large. Limit: {limit_mb:.1f} MB."
        )

    provider = _post_ocr(
        settings=settings,
        image_base64=base64.b64encode(raw).decode("ascii"),
        mime_type=mime,
        prompt=(prompt or "").strip() or DEFAULT_PROMPT,
    )
    text = _clean_text(provider["content"])
    return {
        "text": text,
        "lines": [line for line in text.splitlines() if line.strip()],
        "model": settings.ocr_model,
        "usage": provider["usage"],
    }


def _base_url(settings: Settings) -> str:
    return (settings.ocr_base_url or settings.llm_base_url or "").strip().rstrip("/")


def _api_key(settings: Settings) -> str:
    return (settings.ocr_api_key or settings.llm_api_key or "").strip()


def _chat_completion_url(base_url: str) -> str:
    """兼容模式 base 需要带 /v1，这里只在未写全路径时补 /chat/completions。"""
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _decode_image(image_base64: str, settings: Settings) -> bytes:
    """去掉 data URL 前缀并做 base64 解码；先按文本长度粗筛，避免解码超大字符串。"""
    value = (image_base64 or "").strip()
    if not value:
        raise OcrRequestError("image_base64 is required.")
    value = _DATA_URL_PREFIX.sub("", value)
    # base64 长度与原始字节约为 4:3，粗筛可挡住明显的超大请求，免得白解码几十 MB。
    if len(value) > settings.ocr_max_image_bytes * 2:
        limit_mb = settings.ocr_max_image_bytes / 1024 / 1024
        raise OcrRequestError(f"Image is too large. Limit: {limit_mb:.1f} MB.")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OcrRequestError("image_base64 is not valid base64.") from exc


def _post_ocr(
    *,
    settings: Settings,
    image_base64: str,
    mime_type: str,
    prompt: str,
) -> dict[str, Any]:
    # OCR 默认复用 LLM 那把钥匙，所以与 LLM 共用同一个配额桶计费记账。
    charge_llm_call()

    payload = {
        "model": settings.ocr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 1500,
        "stream": False,
    }

    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(
                _chat_completion_url(_base_url(settings)),
                headers={
                    "Authorization": f"Bearer {_api_key(settings)}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # 只回传状态码，绝不把上游响应体带进日志或接口，避免密钥/账号信息外泄。
        raise OcrResponseError(f"OCR provider returned an error: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise OcrResponseError("OCR provider request failed.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise OcrResponseError("OCR provider response was not JSON.") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OcrResponseError("OCR response did not contain message content.") from exc

    if not isinstance(content, str) or not content.strip():
        raise OcrResponseError("OCR response content was empty.")
    return {"content": content, "usage": data.get("usage") or {}}


def _clean_text(content: str) -> str:
    """去掉坐标行、Markdown 包裹和首尾空白，得到可直接复制的文本。"""
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    lines = [line.rstrip() for line in stripped.splitlines()]
    kept = [line for line in lines if line.strip() and not _COORDINATE_LINE.match(line)]
    return "\n".join(kept).strip()
