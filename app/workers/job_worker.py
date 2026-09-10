import asyncio
import json
import logging
import uuid

import aio_pika

from app.core.config import get_settings
from app.infrastructure.database import async_session_factory
from app.infrastructure.rabbitmq import JOBS_QUEUE_NAME
from app.models.job import JobStatus
from app.repositories.implementations.job_repository import SQLAlchemyJobRepository
from app.workers.task_registry import get_task_handler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("job_worker")

settings = get_settings()


async def process_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process():
        body = json.loads(message.body.decode())
        job_id = uuid.UUID(body["job_id"])

        async with async_session_factory() as session:
            repository = SQLAlchemyJobRepository(session)
            job = await repository.get_by_id(job_id)

            if job is None:
                logger.warning("Job %s not found, skipping", job_id)
                return

            if job.status == JobStatus.CANCELLED:
                logger.info("Job %s already cancelled, skipping execution", job_id)
                return

            await repository.update_status(job.id, JobStatus.RUNNING)
            await repository.add_log(job.id, f"Started executing task '{job.task_type}'")

            try:
                handler = get_task_handler(job.task_type)

                async def log_fn(msg: str) -> None:
                    await repository.add_log(job.id, msg)

                result = await handler(job.payload, log_fn)

                await repository.update_status(job.id, JobStatus.COMPLETED, result=result)
                await repository.add_log(job.id, "Job completed successfully")

            except Exception as exc:
                logger.exception("Job %s failed", job_id)
                await repository.update_status(job.id, JobStatus.FAILED, error_message=str(exc))
                await repository.add_log(job.id, f"Job failed: {exc}", level="error")


async def main() -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=5)
        queue = await channel.declare_queue(JOBS_QUEUE_NAME, durable=True)

        logger.info("Worker started, waiting for jobs on '%s'", JOBS_QUEUE_NAME)
        await queue.consume(process_message)

        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())