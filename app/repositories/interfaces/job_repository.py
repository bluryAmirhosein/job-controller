import uuid
from abc import ABC, abstractmethod

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
        self, owner_id: uuid.UUID | None, limit: int, offset: int
    ) -> list[Job]: ...

    @abstractmethod
    async def update_status(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        result: dict | None = None,
        error_message: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def add_log(self, job_id: uuid.UUID, message: str, level: str = "info") -> JobLog: ...

    @abstractmethod
    async def list_logs(self, job_id: uuid.UUID) -> list[JobLog]: ...