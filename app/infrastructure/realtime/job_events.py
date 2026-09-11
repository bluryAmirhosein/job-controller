import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.models.job import JobStatus
from app.schemas.job_events import JobEvent


async def publish_job_event(
    redis: Redis,
    *,
    event: str,
    job_id: uuid.UUID,
    created_by_id: uuid.UUID,
    status: JobStatus,
    error_message: str | None = None,
    retry_count: int | None = None,
) -> None:
    """
    Publishes a job status event on two Redis pub/sub channels:
      - job_events:{owner_id} -> the general dashboard WebSocket (/ws/jobs)
      - job_status:{job_id}   -> the single-job WebSocket (/ws/jobs/{job_id})

    Pub/Sub here is fire-and-forget: if no subscriber is connected the
    message is dropped. That's fine because GET /jobs/{id} is always the
    source of truth for status — the WebSocket is just a push layer on top.
    """
    payload = JobEvent(
        event=event,
        job_id=job_id,
        created_by_id=created_by_id,
        status=status,
        error_message=error_message,
        retry_count=retry_count,
        timestamp=datetime.now(timezone.utc),
    )
    message = payload.model_dump_json()

    async with redis.pipeline(transaction=False) as pipe:
        pipe.publish(f"job_events:{created_by_id}", message)
        pipe.publish(f"job_status:{job_id}", message)
        await pipe.execute()