"""
Shared fixtures for all tests.

Assumptions about model fields (adjust here only if wrong; the rest of the
suite doesn't need to change):
  - User: id, email, hashed_password, role, is_active
  - Job: id, task_type, payload, status, created_by_id, idempotency_key,
         created_at, result, error_message, retry_count
  - JobLog: id, job_id, message, level, created_at
"""
import contextlib
import uuid
from datetime import datetime, timezone

import pytest

from app.models.job import Job, JobStatus
from app.models.job_log import JobLog
from app.models.user import User, UserRole
from app.repositories.interfaces.job_repository import IJobRepository
from app.repositories.interfaces.user_repository import IUserRepository


class FakeUserRepository(IUserRepository):
    """
    In-memory implementation of IUserRepository. Simulates real repository
    behavior (instead of mocking method-by-method) so service tests aren't
    coupled to SQLAlchemy implementation details.
    """

    def __init__(self) -> None:
        self._users: dict[uuid.UUID, User] = {}

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self._users.values() if u.email == email), None)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._users.get(user_id)

    async def create(self, user: User) -> User:
        if getattr(user, "id", None) is None:
            user.id = uuid.uuid4()
        # Simulate a SQLAlchemy `Column(default=True)` on is_active: that
        # default is only applied at INSERT time in a real session, not when
        # the Python object is constructed. Since this fake never performs a
        # real INSERT, we must replicate that here — otherwise is_active
        # stays None for callers (e.g. AuthService.register) that don't pass
        # it explicitly, and gets incorrectly treated as "inactive".
        if getattr(user, "is_active", None) is None:
            user.is_active = True
        self._users[user.id] = user
        return user

    async def update_role(self, user_id: uuid.UUID, role: UserRole) -> User | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        user.role = role
        return user


class FakeJobRepository(IJobRepository):
    """
    In-memory implementation of IJobRepository. Mirrors the ordering and
    filtering semantics of SQLAlchemyJobRepository closely enough for
    JobService unit tests, without needing a real Postgres connection.
    """

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, Job] = {}
        self._logs: dict[uuid.UUID, list[JobLog]] = {}

    async def create(self, job: Job) -> Job:
        if getattr(job, "id", None) is None:
            job.id = uuid.uuid4()
        if getattr(job, "created_at", None) is None:
            job.created_at = datetime.now(timezone.utc)
        if getattr(job, "updated_at", None) is None:
            job.updated_at = job.created_at
        if getattr(job, "retry_count", None) is None:
            job.retry_count = 0
        self._jobs[job.id] = job
        self._logs.setdefault(job.id, [])
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        return self._jobs.get(job_id)

    async def get_by_idempotency_key(
        self, owner_id: uuid.UUID, idempotency_key: str
    ) -> Job | None:
        return next(
            (
                job
                for job in self._jobs.values()
                if job.created_by_id == owner_id
                and job.idempotency_key == idempotency_key
            ),
            None,
        )

    async def list_jobs(
        self,
        owner_id: uuid.UUID | None,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[Job]:
        jobs = [
            job
            for job in self._jobs.values()
            if owner_id is None or job.created_by_id == owner_id
        ]
        jobs.sort(key=lambda job: (job.created_at, job.id), reverse=True)

        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            jobs = [
                job
                for job in jobs
                if (job.created_at, job.id) < (cursor_created_at, cursor_id)
            ]

        return jobs[: limit + 1]

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
        job = self._jobs.get(job_id)
        if job is None or job.status not in expected_statuses:
            return False

        job.status = new_status
        if result is not None:
            job.result = result
        if error_message is not None:
            job.error_message = error_message
        if retry_count is not None:
            job.retry_count = retry_count
        return True

    async def add_log(self, job_id: uuid.UUID, message: str, level: str = "info") -> JobLog:
        log_entry = JobLog(job_id=job_id, message=message, level=level)
        self._logs.setdefault(job_id, []).append(log_entry)
        return log_entry

    async def list_logs(self, job_id: uuid.UUID) -> list[JobLog]:
        return list(self._logs.get(job_id, []))

    async def count_by_statuses_for_owner(
        self, owner_id: uuid.UUID, statuses: tuple[JobStatus, ...]
    ) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.created_by_id == owner_id and job.status in statuses
        )

    async def get_oldest_by_status_for_owner(
        self, owner_id: uuid.UUID, status: JobStatus
    ) -> Job | None:
        candidates = [
            job
            for job in self._jobs.values()
            if job.created_by_id == owner_id and job.status == status
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda job: job.created_at)

    def owner_lock(self, owner_id: uuid.UUID):
        return self._noop_lock()

    @contextlib.asynccontextmanager
    async def _noop_lock(self):
        yield


@pytest.fixture
def fake_user_repository() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def fake_job_repository() -> FakeJobRepository:
    return FakeJobRepository()


@pytest.fixture
def make_user():
    def _make_user(
        *,
        user_id: uuid.UUID | None = None,
        email: str = "user@example.com",
        hashed_password: str = "hashed-password",
        role: UserRole = UserRole.USER,
        is_active: bool = True,
    ) -> User:
        return User(
            id=user_id or uuid.uuid4(),
            email=email,
            hashed_password=hashed_password,
            role=role,
            is_active=is_active,
        )

    return _make_user


@pytest.fixture
def make_job():
    def _make_job(
        *,
        job_id: uuid.UUID | None = None,
        task_type: str = "demo_sleep",
        payload: dict | None = None,
        status: JobStatus = JobStatus.PENDING,
        created_by_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        result: dict | None = None,
        error_message: str | None = None,
        retry_count: int = 0,
    ) -> Job:
        # `created_at`/`updated_at` are DB-side defaults on the real Job
        # model (applied on INSERT), so they must be filled in explicitly
        # here — otherwise they stay None and break JobResponse validation,
        # the same issue we hit earlier with User.is_active.
        now = datetime.now(timezone.utc)
        return Job(
            id=job_id or uuid.uuid4(),
            task_type=task_type,
            payload=payload if payload is not None else {},
            status=status,
            created_by_id=created_by_id or uuid.uuid4(),
            idempotency_key=idempotency_key,
            created_at=created_at or now,
            updated_at=updated_at or now,
            result=result,
            error_message=error_message,
            retry_count=retry_count,
        )

    return _make_job