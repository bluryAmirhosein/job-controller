# app/repositories/interfaces/job_repository.py
import uuid
from abc import ABC, abstractmethod
from datetime import datetime

from app.models.job import Job, JobStatus
from app.models.job_log import JobLog


class IJobRepository(ABC):
    @abstractmethod
    async def create(self, job: Job) -> Job: ...

    @abstractmethod
    async def get_by_id(self, job_id: uuid.UUID) -> Job | None: ...

    @abstractmethod
    async def get_by_idempotency_key(
        self, owner_id: uuid.UUID, idempotency_key: str
    ) -> Job | None: ...

    @abstractmethod
    async def list_jobs(
        self,
        owner_id: uuid.UUID | None,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[Job]:
        """Returns up to limit + 1 jobs ordered by (created_at desc, id desc),
        starting strictly after `cursor` (if given). The caller uses the
        extra (limit + 1)-th row only to detect whether another page exists;
        it must not be included in the page returned to the client."""
        ...

    @abstractmethod
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
        """Atomically move the job to new_status only if its current status is
        one of expected_statuses. Returns True if the transition happened,
        False if the job was already in some other state (e.g. cancelled
        concurrently) — in which case the caller must NOT proceed as if the
        transition succeeded.

        retry_count, when provided, is persisted alongside the status change
        (used by the worker to record how many retry attempts a job has gone
        through, including interim updates while it's still RUNNING)."""
        ...

    @abstractmethod
    async def add_log(self, job_id: uuid.UUID, message: str, level: str = "info") -> JobLog: ...

    @abstractmethod
    async def list_logs(self, job_id: uuid.UUID) -> list[JobLog]: ...

    @abstractmethod
    async def count_by_statuses_for_owner(
            self, owner_id: uuid.UUID, statuses: tuple[JobStatus, ...]
    ) -> int: ...

    @abstractmethod
    async def get_oldest_by_status_for_owner(
            self, owner_id: uuid.UUID, status: JobStatus
    ) -> Job | None: ...

    @abstractmethod
    def owner_lock(self, owner_id: uuid.UUID):
        """Async context manager: acquires a PostgreSQL advisory lock
         scoped to the specified owner to serialize concurrent create_job
          requests for the same user."""
        ...