import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.job import JobStatus


class JobCreateRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: uuid.UUID
    task_type: str
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    items: list[JobResponse]
    next_cursor: str | None = None


class JobLogResponse(BaseModel):
    id: uuid.UUID
    level: str
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}