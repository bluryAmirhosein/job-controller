import asyncio
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from redis.asyncio import Redis

from app.core.dependencies import get_job_service
from app.core.ws_auth import authenticate_websocket
from app.infrastructure.redis_client import get_redis_client
from app.models.user import UserRole
from app.services.job_service import JobService

router = APIRouter(tags=["Jobs Realtime"])

PING_INTERVAL_SECONDS = 20


async def _redis_listener(pubsub, websocket: WebSocket) -> None:
    """Reads Redis pub/sub messages and forwards them to the client."""
    while True:
        message = await pubsub.get_message(
            ignore_subscribe_messages=True, timeout=PING_INTERVAL_SECONDS
        )
        if message is None:
            # No event within the timeout window; send a ping to keep
            # the connection alive.
            await websocket.send_json({"event": "ping"})
            continue
        raw = message["data"]
        await websocket.send_text(raw if isinstance(raw, str) else raw.decode())


async def _client_watcher(websocket: WebSocket) -> None:
    """Only used to detect disconnects or incoming client messages."""
    while True:
        await websocket.receive_text()


async def _run_bridge(websocket: WebSocket, pubsub) -> None:
    listener_task = asyncio.create_task(_redis_listener(pubsub, websocket))
    watcher_task = asyncio.create_task(_client_watcher(websocket))
    done, pending = await asyncio.wait(
        {listener_task, watcher_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
            raise exc


@router.websocket("/ws/jobs")
async def watch_all_jobs(
    websocket: WebSocket,
    token: str = Query(...),
    redis: Redis = Depends(get_redis_client),
) -> None:
    user = await authenticate_websocket(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    pubsub = redis.pubsub()
    try:
        if user.role == UserRole.ADMIN:
            await pubsub.psubscribe("job_events:*")
        else:
            await pubsub.subscribe(f"job_events:{user.id}")

        await _run_bridge(websocket, pubsub)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.close()


@router.websocket("/ws/jobs/{job_id}")
async def watch_single_job(
    websocket: WebSocket,
    job_id: uuid.UUID,
    token: str = Query(...),
    redis: Redis = Depends(get_redis_client),
    job_service: JobService = Depends(get_job_service),
) -> None:
    user = await authenticate_websocket(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    job = await job_service.get_job(job_id, user)
    if job is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Send the current status right away so a change that happens between
    # connect and the first pub/sub message isn't missed.
    await websocket.send_json(
        {
            "event": "job.snapshot",
            "job_id": str(job.id),
            "status": job.status.value,
            "error_message": job.error_message,
            "retry_count": job.retry_count,
        }
    )

    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(f"job_status:{job_id}")
        await _run_bridge(websocket, pubsub)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.close()