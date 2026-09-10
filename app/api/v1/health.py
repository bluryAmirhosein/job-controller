from fastapi import APIRouter

from app.infrastructure.database import check_database_health
from app.infrastructure.rabbitmq import check_rabbitmq_health
from app.infrastructure.redis_client import check_redis_health
from app.schemas.health import DependencyStatus, HealthCheckResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    db_ok = await check_database_health()
    redis_ok = await check_redis_health()
    rabbitmq_ok = await check_rabbitmq_health()

    overall_status = "ok" if all([db_ok, redis_ok, rabbitmq_ok]) else "degraded"

    return HealthCheckResponse(
        status=overall_status,
        dependencies=DependencyStatus(
            database=db_ok,
            redis=redis_ok,
            rabbitmq=rabbitmq_ok,
        ),
    )