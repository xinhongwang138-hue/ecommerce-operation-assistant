from celery import Celery

from app.core.config import get_settings


settings = get_settings()

celery_app = Celery(
    "ecommerce_operation",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_default_queue=settings.celery_task_default_queue,
    task_routes={
        "generation.*": {"queue": settings.celery_task_default_queue},
        "agent.run_product_operation": {"queue": settings.celery_agent_queue},
        "agent.run_registered_workflow": {"queue": settings.celery_agent_workflow_queue},
        "maintenance.*": {"queue": settings.celery_maintenance_queue},
        # 知识库导入单独成列：MinerU 解析 + 本地向量化是分钟级任务，和 generation
        # 混跑会把素材生成饿死，和 agent 混跑又会被那两个任务的 1700/1800s 时间限制
        # 拦腰砍断——所以这里只加路由，不给 knowledge.* 设 task_annotations。
        "knowledge.*": {"queue": settings.celery_knowledge_queue},
    },
    task_track_started=True,
    # 任务执行成功后再确认消息，worker 崩溃时由队列重投，配合数据库状态避免生成任务无声丢失。
    task_acks_late=True,
    # Worker 子进程异常退出时重新投递；数据库幂等与 lease fencing 负责吸收重复消息。
    task_reject_on_worker_lost=True,
    # 每个 worker 进程一次只预取一个任务，防止耗时素材生成把后续任务长期占住。
    worker_prefetch_multiplier=1,
    task_annotations={
        "agent.run_product_operation": {"soft_time_limit": 1700, "time_limit": 1800},
        "agent.run_registered_workflow": {"soft_time_limit": 1700, "time_limit": 1800},
    },
    beat_schedule={
        "dispatch-agent-outbox": {
            "task": "maintenance.dispatch_outbox",
            "schedule": 5.0,
            "options": {"queue": settings.celery_maintenance_queue},
        },
        "maintain-agent-runtime": {
            "task": "maintenance.agent_runtime",
            "schedule": 30.0,
            "options": {"queue": settings.celery_maintenance_queue},
        },
    },
)
