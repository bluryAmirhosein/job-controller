# app/repositories/implementations/job_repository.py
import uuid
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
import contextlib

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

    async def transition_status(
        self,
        job_id: uuid.UUID,
        *,
        expected_statuses: tuple[JobStatus, ...],
        new_status: JobStatus,
        result: dict | None = None,
        error_message: str | None = None,
        retry_count: int | None = None,
    ) -> bool:
        values: dict = {"status": new_status}
        if result is not None:
            values["result"] = result
        if error_message is not None:
            values["error_message"] = error_message
        if retry_count is not None:
            values["retry_count"] = retry_count

        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status.in_(expected_statuses))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        exec_result = await self._session.execute(stmt)
        await self._session.commit()
        return exec_result.rowcount > 0

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

    async def count_by_statuses_for_owner(
        self, owner_id: uuid.UUID, statuses: tuple[JobStatus, ...]
    ) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Job).where(
                Job.created_by_id == owner_id,
                Job.status.in_(statuses),
            )
        )
        return result.scalar_one()

    async def get_oldest_by_status_for_owner(
        self, owner_id: uuid.UUID, status: JobStatus
    ) -> Job | None:
        result = await self._session.execute(
            select(Job)
            .where(Job.created_by_id == owner_id, Job.status == status)
            .order_by(Job.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def owner_lock(self, owner_id: uuid.UUID):
        return self._owner_lock(owner_id)

    @contextlib.asynccontextmanager
    async def _owner_lock(self, owner_id: uuid.UUID):
        key_result = await self._session.execute(select(func.hashtext(str(owner_id))))
        lock_key = key_result.scalar_one()
        await self._session.execute(select(func.pg_advisory_lock(lock_key)))
        try:
            yield
        finally:
            await self._session.execute(select(func.pg_advisory_unlock(lock_key)))