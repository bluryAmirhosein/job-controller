import uuid
import json
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from redis.asyncio import Redis

from app.infrastructure.cache.job_list_cache import (
    ADMIN_SCOPE,
    get_cached_job_list,
    get_job_list_version,
    set_cached_job_list,
)
from app.infrastructure.redis_client import get_redis_client
from app.core.dependencies import get_current_user, get_job_service
from app.models.user import User
from app.schemas.job import JobCreateRequest, JobLogResponse, JobResponse
from app.services.job_service import JobService
from app.core.rate_limiter import rate_limit
from app.models.user import UserRole


router = APIRouter(prefix="/jobs", tags=["Jobs"])

create_job_rate_limit = rate_limit(
    max_requests=10, window_seconds=60, key_prefix="rate_limit:jobs:create"
)

@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(create_job_rate_limit)],
)
async def create_job(
    payload: JobCreateRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    job_service: JobService = Depends(get_job_service),
) -> JobResponse:
    try:
        job, created = await job_service.create_job(payload, current_user, idempotency_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JobResponse.model_validate(job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        current_user: User = Depends(get_current_user),
        job_service: JobService = Depends(get_job_service),
        redis: Redis = Depends(get_redis_client),
) -> list[JobResponse]:
    scope = ADMIN_SCOPE if current_user.role == UserRole.ADMIN else str(current_user.id)
    version = await get_job_list_version(redis, scope)

    cached = await get_cached_job_list(redis, scope, version, limit, offset)
    if cached is not None:
        # Already shaped like JobResponse; FastAPI validates/serializes
        # it against response_model on the way out.
        return json.loads(cached)

    jobs = await job_service.list_jobs(current_user, limit=limit, offset=offset)
    responses = [JobResponse.model_validate(job) for job in jobs]

    payload = json.dumps([r.model_dump(mode="json") for r in responses])
    await set_cached_job_list(redis, scope, version, limit, offset, payload)

    return responses


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    job_service: JobService = Depends(get_job_service),
) -> JobResponse:
    job = await job_service.get_job(job_id, current_user)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobResponse.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    job_service: JobService = Depends(get_job_service),
) -> JobResponse:
    try:
        job = await job_service.cancel_job(job_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobResponse.model_validate(job)


@router.get("/{job_id}/logs", response_model=list[JobLogResponse])
async def get_job_logs(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    job_service: JobService = Depends(get_job_service),
) -> list[JobLogResponse]:
    logs = await job_service.get_logs(job_id, current_user)
    if logs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return [JobLogResponse.model_validate(log) for log in logs]