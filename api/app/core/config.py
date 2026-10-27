from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

API_DIR = Path(__file__).resolve().parents[2]



def _is_placeholder_or_weak(value: str,*,min_length: int) -> bool:
    normalized = value.strip().lower()
    return len(value) < min_length or normalized.startswith("replace_with_") or normalized in {"agent","password"}


class Settings(BaseSettings):

    app_name: str = "Ecommerce Operation Assistant API"
    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(..., alias="DATABASE_URL")
    api_cors_origins: str = Field(default="http://localhost:3000,http://127.0.0.1:3000", alias="API_CORS_ORIGINS")
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    task_queue_backend: str = Field(default="inline", alias="TASK_QUEUE_BACKEND")
    celery_task_default_queue: str = Field(default="generation", alias="CELERY_TASK_DEFAULT_QUEUE")
    celery_agent_queue: str = Field(default="agent", alias="CELERY_AGENT_QUEUE")
    celery_agent_workflow_queue: str = Field(default="agent.workflow", alias="CELERY_AGENT_WORKFLOW_QUEUE")
    celery_maintenance_queue: str = Field(default="maintenance", alias="CELERY_MAINTENANCE_QUEUE")
    agent_checkpoint_database_url: str | None = Field(default=None, alias="AGENT_CHECKPOINT_DATABASE_URL")
    agent_checkpoint_host: str = Field(default="127.0.0.1", alias="AGENT_CHECKPOINT_HOST")
    agent_checkpoint_port: int = Field(default=5433, alias="AGENT_CHECKPOINT_PORT")
    agent_checkpoint_user: str = Field(default="agent", alias="AGENT_CHECKPOINT_USER")
    agent_checkpoint_password: str = Field(default="agent", alias="AGENT_CHECKPOINT_PASSWORD")
    agent_checkpoint_database: str = Field(default="agent_checkpoints", alias="AGENT_CHECKPOINT_DATABASE")
    agent_max_total_tokens: int = Field(default=12000, alias="AGENT_MAX_TOTAL_TOKENS")
    agent_max_revisions: int = Field(default=1, alias="AGENT_MAX_REVISIONS")
    otel_service_name: str = Field(default="ecommerce-operation-agent", alias="OTEL_SERVICE_NAME")
    otel_exporter_otlp_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    auth_secret_key: str = Field(default="dev-auth-secret-change-me", alias="AUTH_SECRET_KEY")
    auth_token_expire_minutes: int = Field(default=60 * 24 * 7, alias="AUTH_TOKEN_EXPIRE_MINUTES")
    # 安全基线：限流/登录锁定/请求体上限。开发与测试环境自动关闭限流，避免影响本地调试。
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=240, alias="RATE_LIMIT_PER_MINUTE")
    login_max_attempts: int = Field(default=8, alias="LOGIN_MAX_ATTEMPTS")
    login_lockout_seconds: int = Field(default=300, alias="LOGIN_LOCKOUT_SECONDS")
    max_request_bytes: int = Field(default=16 * 1024 * 1024, alias="MAX_REQUEST_BYTES")
    # 花费配额：LLM 与 OCR 共用一个桶（OCR 默认复用 LLM 那把钥匙）。0 表示不限制。
    # 与上面的限流是两回事——限流按客户端 IP 且开发环境默认关闭，挡不住跨租户盗刷；
    # 配额守在真正产生费用的外呼出口，请求路径与 Celery 后台任务都会被计入。
    llm_daily_calls_per_workspace: int = Field(default=300, alias="LLM_DAILY_CALLS_PER_WORKSPACE")
    llm_daily_calls_total: int = Field(default=1000, alias="LLM_DAILY_CALLS_TOTAL")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_model: str | None = Field(default=None, alias="LLM_MODEL")
    # 图片取字（OCR）单列一组配置：默认沿用文本模型的地址与密钥，只换模型名，
    # 这样接入方只要多配一个 OCR_MODEL 就能用，不必重复填 key。
    ocr_model: str = Field(default="qwen-vl-ocr-2025-11-20", alias="OCR_MODEL")
    ocr_base_url: str | None = Field(default=None, alias="OCR_BASE_URL")
    ocr_api_key: str | None = Field(default=None, alias="OCR_API_KEY")
    ocr_max_image_bytes: int = Field(default=8 * 1024 * 1024, alias="OCR_MAX_IMAGE_BYTES")
    image_provider: str | None = Field(default="jimeng", alias="IMAGE_PROVIDER")
    image_base_url: str | None = Field(default="https://visual.volcengineapi.com", alias="IMAGE_BASE_URL")
    image_api_key: str | None = Field(default=None, alias="IMAGE_API_KEY")
    image_model: str | None = Field(default="jimeng_t2i_v30", alias="IMAGE_MODEL")
    image_size: str = Field(default="1024*1024", alias="IMAGE_SIZE")
    image_workspace_id: str | None = Field(default=None, alias="IMAGE_WORKSPACE_ID")
    image_poll_interval_seconds: int = Field(default=5, alias="IMAGE_POLL_INTERVAL_SECONDS")
    image_poll_max_attempts: int = Field(default=24, alias="IMAGE_POLL_MAX_ATTEMPTS")
    video_provider: str | None = Field(default="jimeng", alias="VIDEO_PROVIDER")
    video_base_url: str | None = Field(default="https://visual.volcengineapi.com", alias="VIDEO_BASE_URL")
    video_api_key: str | None = Field(default=None, alias="VIDEO_API_KEY")
    video_model: str | None = Field(default="jimeng_t2v_v30", alias="VIDEO_MODEL")
    video_size: str = Field(default="1280*720", alias="VIDEO_SIZE")
    video_duration: int = Field(default=5, alias="VIDEO_DURATION")
    video_workspace_id: str | None = Field(default=None, alias="VIDEO_WORKSPACE_ID")
    video_poll_interval_seconds: int = Field(default=10, alias="VIDEO_POLL_INTERVAL_SECONDS")
    video_poll_max_attempts: int = Field(default=36, alias="VIDEO_POLL_MAX_ATTEMPTS")
    # 数字人（魔珐星云）凭证：只存后端 .env，经 GET /api/v1/digital-human/credentials 下发前端，
    # 避免 AppSecret 进入前端源码。留空时接口返回空串，前端提示未配置而不报错。
    digital_human_app_id: str = Field(default="", alias="DIGITAL_HUMAN_APP_ID")
    digital_human_app_secret: str = Field(default="", alias="DIGITAL_HUMAN_APP_SECRET")
    digital_human_gateway_server: str = Field(
        default="https://nebula-agent.xingyun3d.com/user/v1/ttsa/session",
        alias="DIGITAL_HUMAN_GATEWAY_SERVER",
    )

    #核心方法是读取本地的.env文件覆盖当前对象中的变量值 
    # 推荐写成绝对路径的形式 指定固定的.env文件
    # 逐个加载：.env 为基线（对齐 docker-compose），.env.local 为可选的本机覆盖（后者优先）。
    # 缺失的文件会被自动跳过，因此生产环境没有 .env.local 也能正常启动。
    model_config = SettingsConfigDict(
        env_file=(API_DIR / ".env", API_DIR / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #特殊参数处理
    #1. 处理跨域来源 => 把一个长字符串拆分为多个来源
    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def rate_limiting_active(self) -> bool:
        """生产环境默认开启限流；开发/测试环境默认关闭，便于本地联调与自动化测试。"""
        if not self.rate_limit_enabled:
            return False
        return self.app_env.lower() not in {"development", "dev", "test", "testing"}

    #2. 检查点数据库地址
    @property
    def checkpoint_database_url(self) -> str:
        if self.agent_checkpoint_database_url:
            return self.agent_checkpoint_database_url
        return f"postgresql://{self.agent_checkpoint_user}:{self.agent_checkpoint_password}@{self.agent_checkpoint_host}:{self.agent_checkpoint_port}/{self.agent_checkpoint_database}"

    #后置参数校验
    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        #1. 循环复查次数
        if self.agent_max_revisions < 0 or self.agent_max_revisions > 3:
            raise ValueError("AGENT_MAX_REVISIONS must be between 0 and 3")

        #2. agent的最大token数
        if self.agent_max_total_tokens < 1000:
            raise ValueError("AGENT_MAX_TOTAL_TOKENS must be at least 1000")

        #3. 生产环境下必须设置安全等级够高的密钥
        if self.app_env.lower() in {"production", "prod"}:
            # 保证令牌密码的安全等级
            if _is_placeholder_or_weak(self.auth_secret_key, min_length=32):
                raise ValueError("AUTH_SECRET_KEY must be set to a strong value in production")
            # 获取配置文件的检查点账号密码
            checkpoint_password = self.agent_checkpoint_password
            if self.checkpoint_database_url:
                try:
                    checkpoint_password = make_url(self.checkpoint_database_url).password or ""
                except Exception as exc:
                    raise ValueError(f"Invalid AGENT_CHECKPOINT_DATABASE_URL: {exc}") from exc
            if _is_placeholder_or_weak(checkpoint_password, min_length=16):
                raise ValueError("AGENT_CHECKPOINT_PASSWORD must be set to a strong value in production")
        return self


# 单例创建Settings  只有在第一次调用的时候会执行 后续的调用会直接使用第一次执行的结果
@lru_cache
def get_settings() -> Settings:
    # print("hello")
    return Settings()
