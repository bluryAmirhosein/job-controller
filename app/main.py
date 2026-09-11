from fastapi import FastAPI

from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.jobs import router as jobs_router
from app.core.config import get_settings
from app.infrastructure.rabbitmq import close_rabbitmq_connection
from app.api.websockets.jobs_ws import router as jobs_ws_router

settings = get_settings()

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")

app.include_router(jobs_ws_router)

@app.on_event("shutdown")
async def shutdown_event() -> None:
    await close_rabbitmq_connection()