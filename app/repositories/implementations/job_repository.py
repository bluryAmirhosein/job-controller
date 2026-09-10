import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.models.job_log import JobLog
from app.repositories.interfaces.job_repository import IJobRepository


class SQLAlchemyJobRepository(IJobRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, job: Job) -> Job:
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        result = await self._session.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, owner_id: uuid.UUID, idempotency_key: str
    ) -> Job | None:
        result = await self._session.execute(
            select(Job).where(
                Job.created_by_id == owner_id,
                Job.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_jobs(
        self, owner_id: uuid.UUID | None, limit: int, offset: int
    ) -> list[Job]:
        query = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
        if owner_id is not None:
            query = query.where(Job.created_by_id == owner_id)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        result: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        job = await self.get_by_id(job_id)
        if job is None:
            return
        job.status = status
        if result is not None:
            job.result = result
        if error_message is not None:
            job.error_message = error_message
        await self._session.commit()

    async def add_log(self, job_id: uuid.UUID, message: str, level: str = "info") -> JobLog:
        log_entry = JobLog(job_id=job_id, message=message, level=level)
        self._session.add(log_entry)
        await self._session.commit()
        await self._session.refresh(log_entry)
        return log_entry

    async def list_logs(self, job_id: uuid.UUID) -> list[JobLog]:
        result = await self._session.execute(
            select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.created_at.asc())
        )
        return list(result.scalars().all())