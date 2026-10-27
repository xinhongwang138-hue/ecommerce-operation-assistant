import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.middleware import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from app.api.routes import agent_platform, agent_runs, model_settings, auth, health, ocr, products, promotion_links, stores, users, workspace
from app.api.routes import chat_ws, digital_human, uploads
from app.core.config import API_DIR, get_settings
from app.services.spend_quota import LLMBudgetExceeded

settings = get_settings()
# 生产环境关闭 Swagger/OpenAPI，避免把完整接口面暴露给匿名访问者。
_is_production = settings.app_env.lower() in {"production", "prod"}
logger = logging.getLogger("app.startup")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 限流在非生产环境默认关闭（见 Settings.rate_limiting_active）。风险不在开发本身，
    # 而在于这份 .env 被原样搬上服务器——那时全站无节流却没有任何提示。
    if not settings.rate_limiting_active:
        logger.warning(
            "限流未启用（APP_ENV=%s），登录爆破与 LLM/OCR 成本盗刷无防护；"
            "对外提供服务前必须把 APP_ENV 设为 production。",
            settings.app_env,
        )
    if not _is_production:
        logger.warning("非生产模式：/docs 与 /openapi.json 处于开放状态。")
    yield


app = FastAPI(
    title=settings.app_name + ' — 第05篇 Agent运营助手',
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)


@app.exception_handler(LLMBudgetExceeded)
async def _handle_llm_budget_exceeded(_request: Request, exc: LLMBudgetExceeded) -> JSONResponse:
    """配额超限统一转 429。

    守卫设在服务层外呼出口，所以这里能兜住任何一条走到模型调用的路由，
    不必给每个端点单独挂依赖。Celery 侧不经过这里，表现为任务直接失败。
    """
    return JSONResponse(
        {"detail": str(exc)},
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": "3600"},
    )


# 添加顺序即由内到外，CORS 最后添加以覆盖全部响应（含限流 429）。
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(health.router)
for route in [agent_platform, agent_runs, model_settings, auth, products, promotion_links, stores, users, workspace, ocr]:
    app.include_router(route.router, prefix='/api/v1')
for route in [digital_human, chat_ws]:
    app.include_router(route.router, prefix='/api/v1')
# 上传文件改为受登录保护的受控读取，不再以 StaticFiles 匿名暴露。
app.include_router(uploads.router)
(API_DIR / 'uploads').mkdir(parents=True, exist_ok=True)
