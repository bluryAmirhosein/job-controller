import uuid

from app.core.pagination import decode_cursor, encode_cursor
from app.infrastructure.rabbitmq import publish_job_message
from app.models.job import Job, JobStatus
from app.models.job_log import JobLog
from app.models.user import User, UserRole
from app.repositories.interfaces.job_repository import IJobRepository
from app.schemas.job import JobCreateRequest


MAX_CONCURRENT_RUNNING_JOBS = 3

class JobService:
    def __init__(self, job_repository: IJobRepository):
        self._job_repository = job_repository

    async def create_job(
        self,
        data: JobCreateRequest,
        current_user: User,
        idempotency_key: str | None,
    ) -> tuple[Job, bool]:
        if idempotency_key:
            existing_job = await self._job_repository.get_by_idempotency_key(
                current_user.id, idempotency_key
            )
            if existing_job is not None:
                return existing_job, False

        job = Job(
            task_type=data.task_type,
            payload=data.payload,
            status=JobStatus.PENDING,
            created_by_id=current_user.id,
            idempotency_key=idempotency_key,
        )

        async with self._job_repository.owner_lock(current_user.id):
            # commit=False: everything below stays in the same transaction
            # that holds the advisory xact lock, so the concurrency check
            # (count_by_statuses_for_owner) is actually protected by it.
            job = await self._job_repository.create(job, commit=False)

            active_count = await self._job_repository.count_by_statuses_for_owner(
                current_user.id, (JobStatus.PENDING, JobStatus.RUNNING)
            )

            can_dispatch_now = active_count <= MAX_CONCURRENT_RUNNING_JOBS

            if can_dispatch_now:
                try:
                    await publish_job_message(str(job.id))
                except Exception as exc:
                    # Commit explicitly here: we want the job row + FAILED
                    # status to survive even though we're about to raise
                    # (which would otherwise trigger a rollback inside
                    # owner_lock and wipe the job out entirely).
                    await self._job_repository.transition_status(
                        job.id,
                        expected_statuses=(JobStatus.PENDING,),
                        new_status=JobStatus.FAILED,
                        error_message=f"Failed to publish job to queue: {exc}",
                        commit=True,
                    )
                    raise RuntimeError("Failed to enqueue job") from exc
            else:
                await self._job_repository.transition_status(
                    job.id,
                    expected_statuses=(JobStatus.PENDING,),
                    new_status=JobStatus.QUEUED,
                    commit=False,
                )
                job.status = JobStatus.QUEUED

        return job, True

    async def promote_next_queued_job(self, owner_id: uuid.UUID | None) -> None:
        if owner_id is None:
            return

        next_job = await self._job_repository.get_oldest_by_status_for_owner(
            owner_id, JobStatus.QUEUED
        )
        if next_job is None:
            return

        transitioned = await self._job_repository.transition_status(
            next_job.id,
            expected_statuses=(JobStatus.QUEUED,),
            new_status=JobStatus.PENDING,
        )
        if not transitioned:
            return

        try:
            await publish_job_message(str(next_job.id))
        except Exception as exc:
            await self._job_repository.transition_status(
                next_job.id,
                expected_statuses=(JobStatus.PENDING,),
                new_status=JobStatus.QUEUED,
            )
            await self._job_repository.add_log(
                next_job.id, f"Failed to dispatch queued job: {exc}", level="error"
            )
            return

        await self._job_repository.add_log(
            next_job.id, "Dispatched after a running slot freed up"
        )

    async def get_job(self, job_id: uuid.UUID, current_user: User) -> Job | None:
        job = await self._job_repository.get_by_id(job_id)
        if job is None:
            return None
        if current_user.role != UserRole.ADMIN and job.created_by_id != current_user.id:
            return None
        return job

    async def list_jobs(
        self, current_user: User, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[Job], str | None]:
        owner_id = None if current_user.role == UserRole.ADMIN else current_user.id

        decoded_cursor = decode_cursor(cursor) if cursor is not None else None

        jobs = await self._job_repository.list_jobs(
            owner_id=owner_id, limit=limit, cursor=decoded_cursor
        )

        has_more = len(jobs) > limit
        page = jobs[:limit]
        next_cursor = encode_cursor(page[-1]) if has_more and page else None

        return page, next_cursor

    async def cancel_job(self, job_id: uuid.UUID, current_user: User) -> Job | None:
        job = await self.get_job(job_id, current_user)
        if job is None:
            return None

        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            raise ValueError(f"Cannot cancel a job with status '{job.status.value}'")

        was_running = job.status == JobStatus.RUNNING

        transitioned = await self._job_repository.transition_status(
            job.id,
            expected_statuses=(JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING),
            new_status=JobStatus.CANCELLED,
        )
        if not transitioned:
            job = await self.get_job(job_id, current_user)
            raise ValueError(f"Cannot cancel a job with status '{job.status.value}'")

        job.status = JobStatus.CANCELLED
        await self._job_repository.add_log(job.id, "Job cancelled by user request")

        if was_running:
            await self.promote_next_queued_job(job.created_by_id)

        return job

    async def get_logs(self, job_id: uuid.UUID, current_user: User) -> list[JobLog] | None:
        job = await self.get_job(job_id, current_user)
        if job is None:
            return None
        return await self._job_repository.list_logs(job_id)