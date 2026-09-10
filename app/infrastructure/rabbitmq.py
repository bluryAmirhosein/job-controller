import json

import aio_pika
from aio_pika import DeliveryMode, Message

from app.core.config import get_settings

settings = get_settings()

JOBS_QUEUE_NAME = "jobs_queue"

_connection: aio_pika.RobustConnection | None = None


async def get_rabbitmq_connection() -> aio_pika.RobustConnection:
    global _connection
    if _connection is None or _connection.is_closed:
        _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    return _connection


async def check_rabbitmq_health() -> bool:
    try:
        connection = await get_rabbitmq_connection()
        return not connection.is_closed
    except Exception:
        return False


async def close_rabbitmq_connection() -> None:
    global _connection
    if _connection is not None and not _connection.is_closed:
        await _connection.close()


async def publish_job_message(job_id: str) -> None:
    connection = await get_rabbitmq_connection()
    async with connection.channel() as channel:
        queue = await channel.declare_queue(JOBS_QUEUE_NAME, durable=True)
        message = Message(
            body=json.dumps({"job_id": job_id}).encode(),
            delivery_mode=DeliveryMode.PERSISTENT,
        )
        await channel.default_exchange.publish(message, routing_key=queue.name)