import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.job import JobStatus


class JobEvent(BaseModel):
    event: str  # "job.created" | "job.status_changed"
    job_id: uuid.UUID
    created_by_id: uuid.UUID
    status: JobStatus
    error_message: str | None = None
    retry_count: int | None = None
    timestamp: datetime