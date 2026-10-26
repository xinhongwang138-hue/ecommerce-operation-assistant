from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.middleware import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from app.api.routes import agent_platform, agent_runs, model_settings, auth, health, ocr, products, promotion_links, stores, users, workspace
from app.api.routes import chat_ws, digital_human, uploads
from app.core.config import API_DIR, get_settings

settings = get_settings()
# 生产环境关闭 Swagger/OpenAPI，避免把完整接口面暴露给匿名访问者。
_is_production = settings.app_env.lower() in {"production", "prod"}

app = FastAPI(
    title=settings.app_name + ' — 第05篇 Agent运营助手',
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
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
