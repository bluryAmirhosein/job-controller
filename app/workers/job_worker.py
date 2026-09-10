# app/workers/job_worker.py
import asyncio
import contextlib
import json
import logging
import uuid
import aio_pika

from app.services.job_service import JobService
from app.core.config import get_settings
from app.infrastructure.database import async_session_factory
from app.infrastructure.rabbitmq import JOBS_QUEUE_NAME
from app.models.job import JobStatus
from app.repositories.implementations.job_repository import SQLAlchemyJobRepository
from app.workers.task_registry import get_task_handler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("job_worker")

settings = get_settings()

CANCEL_POLL_INTERVAL_SECONDS = 2.0


async def _watch_for_cancellation(job_id: uuid.UUID, cancel_event: asyncio.Event) -> None:
    """Runs alongside the handler; polls the DB on its own session so it
    always sees committed writes from the cancel endpoint (not a stale
    snapshot from a long-lived transaction)."""
    while True:
        await asyncio.sleep(CANCEL_POLL_INTERVAL_SECONDS)
        async with async_session_factory() as session:
            repository = SQLAlchemyJobRepository(session)
            job = await repository.get_by_id(job_id)
            if job is not None and job.status == JobStatus.CANCELLED:
                cancel_event.set()
                return


async def process_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process():
        body = json.loads(message.body.decode())
        job_id = uuid.UUID(body["job_id"])

        async with async_session_factory() as session:
            repository = SQLAlchemyJobRepository(session)
            job_service = JobService(repository)
            job = await repository.get_by_id(job_id)

            if job is None:
                logger.warning("Job %s not found, skipping", job_id)
                return

            claimed = await repository.transition_status(
                job_id,
                expected_statuses=(JobStatus.PENDING,),
                new_status=JobStatus.RUNNING,
            )
            if not claimed:
                logger.info(
                    "Job %s could not be claimed (status=%s), skipping",
                    job_id, job.status.value,
                )
                return

            await repository.add_log(job.id, f"Started executing task '{job.task_type}'")

            async def log_fn(msg: str) -> None:
                await repository.add_log(job.id, msg)

            cancel_event = asyncio.Event()
            watcher = asyncio.create_task(_watch_for_cancellation(job_id, cancel_event))
            handler = get_task_handler(job.task_type)
            handler_task = asyncio.create_task(handler(job.payload, log_fn))

            try:
                waiter = asyncio.create_task(cancel_event.wait())
                done, _pending = await asyncio.wait(
                    {handler_task, waiter}, return_when=asyncio.FIRST_COMPLETED
                )

                if cancel_event.is_set():
                    handler_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await handler_task
                    logger.info("Job %s cancelled during execution", job_id)
                    await repository.add_log(job.id, "Job cancelled during execution", level="warning")
                    await job_service.promote_next_queued_job(job.created_by_id)
                    return

                waiter.cancel()
                result = handler_task.result()

                await repository.transition_status(
                    job_id,
                    expected_statuses=(JobStatus.RUNNING,),
                    new_status=JobStatus.COMPLETED,
                    result=result,
                )
                await repository.add_log(job.id, "Job completed successfully")
                await job_service.promote_next_queued_job(job.created_by_id)

            except Exception as exc:
                logger.exception("Job %s failed", job_id)
                await repository.transition_status(
                    job_id,
                    expected_statuses=(JobStatus.RUNNING,),
                    new_status=JobStatus.FAILED,
                    error_message=str(exc),
                )
                await repository.add_log(job.id, f"Job failed: {exc}", level="error")
                await job_service.promote_next_queued_job(job.created_by_id)
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher


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