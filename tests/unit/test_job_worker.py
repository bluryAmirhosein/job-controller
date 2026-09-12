import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, create_autospec, patch

import pytest

from app.models.job import Job, JobStatus
from app.repositories.interfaces.job_repository import IJobRepository
from app.workers import job_worker
from app.workers.task_registry import TaskValidationError


class FakeIncomingMessage:
    """Minimal stand-in for aio_pika's AbstractIncomingMessage."""

    def __init__(self, body: dict):
        self.body = json.dumps(body).encode("utf-8")

    def process(self):
        @asynccontextmanager
        async def _cm():
            yield

        return _cm()


def make_job_stub(**overrides) -> Job:
    defaults = dict(
        id=uuid.uuid4(),
        task_type="demo_sleep",
        payload={"seconds": 0},
        status=JobStatus.PENDING,
        created_by_id=uuid.uuid4(),
        idempotency_key=None,
        retry_count=0,
        result=None,
        error_message=None,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture
def mock_repository():
    repository = create_autospec(IJobRepository, instance=True)
    # Not exercised by any of these tests; default to "nothing to promote"
    # so promote_next_queued_job() is a harmless no-op unless a test cares.
    repository.get_oldest_by_status_for_owner = AsyncMock(return_value=None)
    return repository


@pytest.fixture
def fake_redis():
    return AsyncMock()


@pytest.fixture(autouse=True)
def patched_worker_dependencies(mock_repository, monkeypatch):
    @asynccontextmanager
    async def fake_session_factory():
        yield AsyncMock()

    monkeypatch.setattr(job_worker, "async_session_factory", fake_session_factory)
    with patch(
        "app.workers.job_worker.SQLAlchemyJobRepository", return_value=mock_repository
    ):
        yield


class TestProcessMessage:
    async def test_skips_when_the_job_is_not_found(self, mock_repository, fake_redis):
        mock_repository.get_by_id = AsyncMock(return_value=None)
        message = FakeIncomingMessage({"job_id": str(uuid.uuid4())})

        await job_worker.process_message(message, fake_redis)

        mock_repository.transition_status.assert_not_awaited()

    async def test_skips_when_the_job_cannot_be_claimed(self, mock_repository, fake_redis):
        job = make_job_stub(status=JobStatus.RUNNING)
        mock_repository.get_by_id = AsyncMock(return_value=job)
        mock_repository.transition_status = AsyncMock(return_value=False)
        message = FakeIncomingMessage({"job_id": str(job.id)})

        await job_worker.process_message(message, fake_redis)

        mock_repository.add_log.assert_not_awaited()

    async def test_marks_the_job_completed_on_a_successful_run(
        self, mock_repository, fake_redis
    ):
        job = make_job_stub()
        mock_repository.get_by_id = AsyncMock(return_value=job)
        mock_repository.transition_status = AsyncMock(return_value=True)
        mock_repository.add_log = AsyncMock()

        async def succeeding_handler(payload, log):
            return {"message": "done"}

        message = FakeIncomingMessage({"job_id": str(job.id)})

        with patch(
            "app.workers.job_worker.get_task_handler", return_value=succeeding_handler
        ):
            await job_worker.process_message(message, fake_redis)

        completed_calls = [
            call
            for call in mock_repository.transition_status.await_args_list
            if call.kwargs.get("new_status") == JobStatus.COMPLETED
        ]
        assert len(completed_calls) == 1
        assert completed_calls[0].kwargs["result"] == {"message": "done"}
        assert completed_calls[0].kwargs["retry_count"] == 0

    async def test_fails_immediately_without_retrying_on_a_validation_error(
        self, mock_repository, fake_redis
    ):
        job = make_job_stub()
        mock_repository.get_by_id = AsyncMock(return_value=job)
        mock_repository.transition_status = AsyncMock(return_value=True)
        mock_repository.add_log = AsyncMock()

        call_count = 0

        async def invalid_payload_handler(payload, log):
            nonlocal call_count
            call_count += 1
            raise TaskValidationError("payload missing 'seconds'")

        message = FakeIncomingMessage({"job_id": str(job.id)})

        with patch(
            "app.workers.job_worker.get_task_handler", return_value=invalid_payload_handler
        ):
            await job_worker.process_message(message, fake_redis)

        assert call_count == 1

        failed_calls = [
            call
            for call in mock_repository.transition_status.await_args_list
            if call.kwargs.get("new_status") == JobStatus.FAILED
        ]
        assert len(failed_calls) == 1
        assert failed_calls[0].kwargs["retry_count"] == 0

    async def test_fails_after_exhausting_all_retries(
        self, mock_repository, fake_redis, monkeypatch
    ):
        # Keep the retry backoff tiny so this test runs fast instead of
        # sleeping for real seconds between attempts.
        monkeypatch.setattr(job_worker, "RETRY_BACKOFF_SECONDS", 0.01)

        job = make_job_stub()
        mock_repository.get_by_id = AsyncMock(return_value=job)
        mock_repository.transition_status = AsyncMock(return_value=True)
        mock_repository.add_log = AsyncMock()

        call_count = 0

        async def always_failing_handler(payload, log):
            nonlocal call_count
            call_count += 1
            raise Exception("boom")

        message = FakeIncomingMessage({"job_id": str(job.id)})

        with patch(
            "app.workers.job_worker.get_task_handler", return_value=always_failing_handler
        ):
            await job_worker.process_message(message, fake_redis)

        assert call_count == job_worker.MAX_RETRIES + 1

        failed_calls = [
            call
            for call in mock_repository.transition_status.await_args_list
            if call.kwargs.get("new_status") == JobStatus.FAILED
        ]
        assert len(failed_calls) == 1
        assert failed_calls[0].kwargs["retry_count"] == job_worker.MAX_RETRIES + 1