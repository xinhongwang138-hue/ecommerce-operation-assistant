from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.routes import agent_platform, agent_runs, model_settings, auth, health, ocr, products, promotion_links, stores, users, workspace
from app.api.routes import chat_ws, digital_human
from app.core.config import API_DIR, get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name + ' — 第05篇 Agent运营助手')
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(health.router)
for route in [agent_platform, agent_runs, model_settings, auth, products, promotion_links, stores, users, workspace, ocr]:
    app.include_router(route.router, prefix='/api/v1')
for route in [digital_human, chat_ws]:
    app.include_router(route.router, prefix='/api/v1')
uploads_dir = API_DIR / 'uploads'
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount('/uploads', StaticFiles(directory=uploads_dir), name='uploads')
