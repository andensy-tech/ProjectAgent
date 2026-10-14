"""
BackgroundTask model — persistent cross-worker task result storage.

Gunicorn multi-worker 环境下，各 Worker 进程内存隔离。
BackgroundTask 表作为共享存储，确保任何 Worker 都能查询到任务结果。
"""

import json
from datetime import datetime
from sqlalchemy import String, Integer, Text, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class BackgroundTask(TimestampMixin, Base):
    """Persistent background task record.

    Used by BackgroundTaskWorker to share task status/results across
    gunicorn worker processes. Each worker writes results here on completion;
    any worker can read status via GET /api/tasks/{task_id}.
    """

    __tablename__ = "background_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UUID
    skill_name: Mapped[str] = mapped_column(String(128), default="")
    user_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    progress: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def set_result(self, result: dict):
        """Store result dict as JSON string."""
        self.result_json = json.dumps(result, ensure_ascii=False)

    def get_result(self) -> dict:
        """Deserialize stored result JSON."""
        try:
            return json.loads(self.result_json) if self.result_json else {}
        except (json.JSONDecodeError, TypeError):
            return {}
