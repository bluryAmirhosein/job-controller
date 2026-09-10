import uuid

from app.infrastructure.rabbitmq import publish_job_message
from app.models.job import Job, JobStatus
from app.models.job_log import JobLog
from app.models.user import User, UserRole
from app.repositories.interfaces.job_repository import IJobRepository
from app.schemas.job import JobCreateRequest


class JobService:
    def __init__(self, job_repository: IJobRepository):
        self._job_repository = job_repository

    async def create_job(
        self,
        data: JobCreateRequest,
        current_user: User,
        idempotency_key: str | None,
    ) -> tuple[Job, bool]:
        """خروجی: (job, created). created=False یعنی job قبلی (idempotent replay) برگردونده شده."""
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
        job = await self._job_repository.create(job)

        try:
            await publish_job_message(str(job.id))
        except Exception as exc:
            await self._job_repository.update_status(
                job.id,
                JobStatus.FAILED,
                error_message=f"Failed to publish job to queue: {exc}",
            )
            raise RuntimeError("Failed to enqueue job") from exc

        await self._job_repository.update_status(job.id, JobStatus.QUEUED)
        job.status = JobStatus.QUEUED

        return job, True

    async def get_job(self, job_id: uuid.UUID, current_user: User) -> Job | None:
        job = await self._job_repository.get_by_id(job_id)
        if job is None:
            return None
        if current_user.role != UserRole.ADMIN and job.created_by_id != current_user.id:
            return None
        return job

    async def list_jobs(
        self, current_user: User, limit: int = 50, offset: int = 0
    ) -> list[Job]:
        owner_id = None if current_user.role == UserRole.ADMIN else current_user.id
        return await self._job_repository.list_jobs(owner_id=owner_id, limit=limit, offset=offset)

    async def cancel_job(self, job_id: uuid.UUID, current_user: User) -> Job | None:
        job = await self.get_job(job_id, current_user)
        if job is None:
            return None

        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            raise ValueError(f"Cannot cancel a job with status '{job.status.value}'")

        await self._job_repository.update_status(job.id, JobStatus.CANCELLED)
        job.status = JobStatus.CANCELLED
        await self._job_repository.add_log(job.id, "Job cancelled by user request")
        return job

    async def get_logs(self, job_id: uuid.UUID, current_user: User) -> list[JobLog] | None:
        job = await self.get_job(job_id, current_user)
        if job is None:
            return None
        return await self._job_repository.list_logs(job_id)