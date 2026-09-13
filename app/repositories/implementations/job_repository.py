# app/repositories/implementations/job_repository.py
import uuid
import contextlib
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy import select, update, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.cache.job_list_cache import ADMIN_SCOPE, bump_job_list_version
from app.infrastructure.realtime.job_events import publish_job_event
from app.models.job import Job, JobStatus
from app.models.job_log import JobLog
from app.repositories.interfaces.job_repository import IJobRepository


class SQLAlchemyJobRepository(IJobRepository):
    def __init__(self, session: AsyncSession, redis: Redis):
        self._session = session
        self._redis = redis

    async def create(self, job: Job, *, commit: bool = True) -> Job:
        self._session.add(job)
        if commit:
            await self._session.commit()
        else:
            # Just flush so job.id/created_at are populated without ending
            # the transaction (needed while we're still inside owner_lock).
            await self._session.flush()
        await self._session.refresh(job)

        await bump_job_list_version(self._redis, str(job.created_by_id))
        await bump_job_list_version(self._redis, ADMIN_SCOPE)

        await publish_job_event(
            self._redis,
            event="job.created",
            job_id=job.id,
            created_by_id=job.created_by_id,
            status=job.status,
            error_message=job.error_message,
            retry_count=job.retry_count,
        )

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
        self,
        owner_id: uuid.UUID | None,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[Job]:
        query = (
            select(Job)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(limit + 1)
        )
        if owner_id is not None:
            query = query.where(Job.created_by_id == owner_id)

        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            query = query.where(
                or_(
                    Job.created_at < cursor_created_at,
                    and_(
                        Job.created_at == cursor_created_at,
                        Job.id < cursor_id,
                    ),
                )
            )

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
        commit: bool = True,
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
            .returning(Job.created_by_id, Job.status, Job.error_message, Job.retry_count)
        )
        exec_result = await self._session.execute(stmt)
        owner_row = exec_result.first()

        if commit:
            await self._session.commit()
        else:
            await self._session.flush()

        if owner_row is None:
            return False

        await bump_job_list_version(self._redis, str(owner_row.created_by_id))
        await bump_job_list_version(self._redis, ADMIN_SCOPE)

        # We use owner_row instead of the local `values` dict, because owner_row
        # reflects the actual full row state after the UPDATE (e.g. when
        # retry_count wasn't passed but its current DB value still matters).
        await publish_job_event(
            self._redis,
            event="job.status_changed",
            job_id=job_id,
            created_by_id=owner_row.created_by_id,
            status=owner_row.status,
            error_message=owner_row.error_message,
            retry_count=owner_row.retry_count,
        )

        return True

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
        # pg_advisory_xact_lock is transaction-scoped: Postgres releases it
        # automatically on COMMIT or ROLLBACK of this session's transaction,
        # no matter what happens to the underlying pooled connection
        # afterwards. This removes the manual lock/unlock pair we used to
        # have with pg_advisory_lock (session-scoped), which under async
        # connection pooling + commits happening mid-block could leave a
        # lock held longer than intended and lead to circular waits
        # (the deadlock you're seeing) between concurrent requests.
        lock_key_result = await self._session.execute(
            select(func.hashtext(str(owner_id)))
        )
        lock_key = lock_key_result.scalar_one()
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

        try:
            yield
        except Exception:
            # Ends the transaction -> releases the advisory lock too.
            await self._session.rollback()
            raise
        else:
            # If the caller already committed (e.g. after handling a
            # publish failure), this is a harmless no-op.
            await self._session.commit()