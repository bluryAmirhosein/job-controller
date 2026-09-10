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
from app.workers.task_registry import get_task_handler, TaskValidationError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("job_worker")

settings = get_settings()

CANCEL_POLL_INTERVAL_SECONDS = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0  # backoff خطی: attempt * RETRY_BACKOFF_SECONDS


async def _watch_for_cancellation(job_id: uuid.UUID, cancel_event: asyncio.Event) -> None:
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

            async def _handle_cancelled(attempt: int, handler_task: asyncio.Task | None = None) -> None:
                if handler_task is not None:
                    handler_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await handler_task
                logger.info("Job %s cancelled during execution (retry_count=%d)", job_id, attempt)
                await repository.transition_status(
                    job_id,
                    expected_statuses=(JobStatus.CANCELLED,),
                    new_status=JobStatus.CANCELLED,
                    retry_count=attempt,
                )
                await repository.add_log(job.id, "Job cancelled during execution", level="warning")
                await job_service.promote_next_queued_job(job.created_by_id)

            async def _fail(error_message: str, attempt: int) -> None:
                await repository.transition_status(
                    job_id,
                    expected_statuses=(JobStatus.RUNNING,),
                    new_status=JobStatus.FAILED,
                    error_message=error_message,
                    retry_count=attempt,
                )
                await job_service.promote_next_queued_job(job.created_by_id)

            try:
                try:
                    handler = get_task_handler(job.task_type)
                except ValueError as exc:
                    await repository.add_log(job.id, f"Job failed: {exc}", level="error")
                    await _fail(str(exc), attempt=0)
                    return

                attempt = 0
                while True:
                    handler_task = asyncio.create_task(handler(job.payload, log_fn))
                    waiter = asyncio.create_task(cancel_event.wait())
                    await asyncio.wait(
                        {handler_task, waiter}, return_when=asyncio.FIRST_COMPLETED
                    )

                    if cancel_event.is_set():
                        await _handle_cancelled(attempt, handler_task)
                        return

                    waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiter

                    try:
                        result = handler_task.result()
                    except TaskValidationError as exc:
                        logger.warning("Job %s failed: invalid payload (%s)", job_id, exc)
                        await repository.add_log(
                            job.id, f"Job failed (invalid payload, no retry): {exc}", level="error"
                        )
                        await _fail(str(exc), attempt=attempt)
                        return
                    except Exception as exc:
                        attempt += 1

                        if attempt > MAX_RETRIES:
                            logger.exception("Job %s failed after %d retries", job_id, MAX_RETRIES)
                            await repository.add_log(
                                job.id,
                                f"Job failed after {MAX_RETRIES} retries (total attempts: {attempt}): {exc}",
                                level="error",
                            )
                            await _fail(str(exc), attempt=attempt)
                            return

                        # مقدار retry_count رو همین الان persist می‌کنیم (status هنوز RUNNING می‌مونه)
                        # تا وضعیت واقعی retry هر Job از بیرون هم قابل مشاهده باشه، نه فقط توی لاگ.
                        await repository.transition_status(
                            job_id,
                            expected_statuses=(JobStatus.RUNNING,),
                            new_status=JobStatus.RUNNING,
                            retry_count=attempt,
                        )
                        await repository.add_log(
                            job.id,
                            f"Attempt {attempt} failed: {exc}. Retrying ({attempt}/{MAX_RETRIES})...",
                            level="warning",
                        )

                        try:
                            await asyncio.wait_for(
                                cancel_event.wait(),
                                timeout=RETRY_BACKOFF_SECONDS * attempt,
                            )
                            await _handle_cancelled(attempt)
                            return
                        except asyncio.TimeoutError:
                            continue
                    else:
                        await repository.transition_status(
                            job_id,
                            expected_statuses=(JobStatus.RUNNING,),
                            new_status=JobStatus.COMPLETED,
                            result=result,
                            retry_count=attempt,
                        )
                        await repository.add_log(
                            job.id,
                            "Job completed successfully"
                            + (f" (after {attempt} retries)" if attempt else ""),
                        )
                        await job_service.promote_next_queued_job(job.created_by_id)
                        return

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