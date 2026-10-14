from fastapi import APIRouter
from app.api import auth, chat, skills, config, upload, memory, dashboard, investigate, alerts, trends, tasks, feishu_bind, notifications, project_plans, scheduled_tasks

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(skills.router)
api_router.include_router(config.router)
api_router.include_router(upload.router)
api_router.include_router(memory.router)
api_router.include_router(dashboard.router)
api_router.include_router(investigate.router)
api_router.include_router(alerts.router)
api_router.include_router(trends.router)
api_router.include_router(tasks.router)
api_router.include_router(feishu_bind.router)
api_router.include_router(notifications.router)
api_router.include_router(project_plans.router)
api_router.include_router(scheduled_tasks.router)
