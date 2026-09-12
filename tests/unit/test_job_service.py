import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.job import JobStatus
from app.models.user import UserRole
from app.schemas.job import JobCreateRequest
from app.services.job_service import MAX_CONCURRENT_RUNNING_JOBS, JobService


@pytest.fixture
def job_service(fake_job_repository):
    return JobService(fake_job_repository)


@pytest.fixture
def publish_job_message_mock():
    with patch(
        "app.services.job_service.publish_job_message", new_callable=AsyncMock
    ) as mock:
        yield mock


class TestCreateJob:
    async def test_returns_existing_job_when_idempotency_key_matches(
        self, job_service, fake_job_repository, make_user, make_job, publish_job_message_mock
    ):
        owner = make_user()
        existing_job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, idempotency_key="key-1")
        )

        payload = JobCreateRequest(task_type="demo_sleep", payload={"seconds": 1})
        job, created = await job_service.create_job(payload, owner, "key-1")

        assert created is False
        assert job.id == existing_job.id
        publish_job_message_mock.assert_not_awaited()

    async def test_dispatches_immediately_when_under_the_concurrency_limit(
        self, job_service, make_user, publish_job_message_mock
    ):
        owner = make_user()
        payload = JobCreateRequest(task_type="demo_sleep", payload={"seconds": 1})

        job, created = await job_service.create_job(payload, owner, None)

        assert created is True
        assert job.status == JobStatus.PENDING
        publish_job_message_mock.assert_awaited_once_with(str(job.id))

    async def test_queues_the_job_when_the_concurrency_limit_is_reached(
        self, job_service, fake_job_repository, make_user, make_job, publish_job_message_mock
    ):
        owner = make_user()
        for _ in range(MAX_CONCURRENT_RUNNING_JOBS):
            await fake_job_repository.create(
                make_job(created_by_id=owner.id, status=JobStatus.RUNNING)
            )

        payload = JobCreateRequest(task_type="demo_sleep", payload={"seconds": 1})
        job, created = await job_service.create_job(payload, owner, None)

        assert created is True
        assert job.status == JobStatus.QUEUED
        publish_job_message_mock.assert_not_awaited()

    async def test_marks_the_job_failed_and_raises_when_publish_fails(
        self, job_service, fake_job_repository, make_user, publish_job_message_mock
    ):
        owner = make_user()
        publish_job_message_mock.side_effect = Exception("broker unreachable")
        payload = JobCreateRequest(task_type="demo_sleep", payload={"seconds": 1})

        with pytest.raises(RuntimeError, match="Failed to enqueue job"):
            await job_service.create_job(payload, owner, None)

        jobs = await fake_job_repository.list_jobs(owner_id=owner.id, limit=10)
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.FAILED
        assert "broker unreachable" in jobs[0].error_message


class TestPromoteNextQueuedJob:
    async def test_does_nothing_when_owner_id_is_none(
        self, job_service, publish_job_message_mock
    ):
        await job_service.promote_next_queued_job(None)

        publish_job_message_mock.assert_not_awaited()

    async def test_does_nothing_when_no_queued_job_exists(
        self, job_service, make_user, publish_job_message_mock
    ):
        owner = make_user()

        await job_service.promote_next_queued_job(owner.id)

        publish_job_message_mock.assert_not_awaited()

    async def test_promotes_the_oldest_queued_job_and_publishes_it(
        self, job_service, fake_job_repository, make_user, make_job, publish_job_message_mock
    ):
        owner = make_user()
        queued_job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.QUEUED)
        )

        await job_service.promote_next_queued_job(owner.id)

        promoted = await fake_job_repository.get_by_id(queued_job.id)
        assert promoted.status == JobStatus.PENDING
        publish_job_message_mock.assert_awaited_once_with(str(queued_job.id))

    async def test_requeues_the_job_and_logs_an_error_when_publish_fails_during_promotion(
        self, job_service, fake_job_repository, make_user, make_job, publish_job_message_mock
    ):
        owner = make_user()
        queued_job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.QUEUED)
        )
        publish_job_message_mock.side_effect = Exception("broker down")

        await job_service.promote_next_queued_job(owner.id)

        job_after = await fake_job_repository.get_by_id(queued_job.id)
        assert job_after.status == JobStatus.QUEUED

        logs = await fake_job_repository.list_logs(queued_job.id)
        assert any("Failed to dispatch queued job" in log.message for log in logs)


class TestGetJob:
    async def test_owner_can_get_their_own_job(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        job = await fake_job_repository.create(make_job(created_by_id=owner.id))

        result = await job_service.get_job(job.id, owner)

        assert result is not None
        assert result.id == job.id

    async def test_admin_can_get_any_users_job(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        job = await fake_job_repository.create(make_job(created_by_id=owner.id))

        result = await job_service.get_job(job.id, admin)

        assert result is not None

    async def test_a_non_owner_non_admin_cannot_get_the_job(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        other_user = make_user(email="other@example.com")
        job = await fake_job_repository.create(make_job(created_by_id=owner.id))

        result = await job_service.get_job(job.id, other_user)

        assert result is None

    async def test_returns_none_when_the_job_does_not_exist(self, job_service, make_user):
        result = await job_service.get_job(uuid.uuid4(), make_user())

        assert result is None


class TestListJobs:
    async def test_admin_sees_jobs_from_all_users(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        user_a = make_user(email="a@example.com")
        user_b = make_user(email="b@example.com")
        await fake_job_repository.create(make_job(created_by_id=user_a.id))
        await fake_job_repository.create(make_job(created_by_id=user_b.id))

        page, next_cursor = await job_service.list_jobs(admin, limit=50)

        assert len(page) == 2
        assert next_cursor is None

    async def test_a_regular_user_only_sees_their_own_jobs(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        user_a = make_user(email="a@example.com")
        user_b = make_user(email="b@example.com")
        await fake_job_repository.create(make_job(created_by_id=user_a.id))
        await fake_job_repository.create(make_job(created_by_id=user_b.id))

        page, _ = await job_service.list_jobs(user_a, limit=50)

        assert len(page) == 1
        assert page[0].created_by_id == user_a.id

    async def test_returns_a_next_cursor_when_more_pages_exist(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        for _ in range(3):
            await fake_job_repository.create(make_job(created_by_id=owner.id))

        page, next_cursor = await job_service.list_jobs(owner, limit=2)

        assert len(page) == 2
        assert next_cursor is not None

    async def test_returns_no_cursor_on_the_last_page(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        await fake_job_repository.create(make_job(created_by_id=owner.id))

        page, next_cursor = await job_service.list_jobs(owner, limit=50)

        assert len(page) == 1
        assert next_cursor is None

    async def test_raises_value_error_for_an_invalid_cursor(self, job_service, make_user):
        with pytest.raises(ValueError, match="Invalid cursor"):
            await job_service.list_jobs(make_user(), limit=50, cursor="not-a-valid-cursor")


class TestCancelJob:
    async def test_cancels_a_pending_job(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.PENDING)
        )

        result = await job_service.cancel_job(job.id, owner)

        assert result.status == JobStatus.CANCELLED
        logs = await fake_job_repository.list_logs(job.id)
        assert any("cancelled" in log.message.lower() for log in logs)

    async def test_cancelling_a_running_job_promotes_the_next_queued_job(
        self, job_service, fake_job_repository, make_user, make_job, publish_job_message_mock
    ):
        owner = make_user()
        running_job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.RUNNING)
        )
        queued_job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.QUEUED)
        )

        await job_service.cancel_job(running_job.id, owner)

        promoted = await fake_job_repository.get_by_id(queued_job.id)
        assert promoted.status == JobStatus.PENDING
        publish_job_message_mock.assert_awaited_once_with(str(queued_job.id))

    @pytest.mark.parametrize(
        "terminal_status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
    )
    async def test_raises_when_the_job_is_already_in_a_terminal_status(
        self, job_service, fake_job_repository, make_user, make_job, terminal_status
    ):
        owner = make_user()
        job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=terminal_status)
        )

        with pytest.raises(ValueError, match="Cannot cancel a job"):
            await job_service.cancel_job(job.id, owner)

    async def test_returns_none_when_the_job_does_not_exist_or_is_not_owned(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        other_user = make_user(email="other@example.com")
        job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.PENDING)
        )

        result = await job_service.cancel_job(job.id, other_user)

        assert result is None

    async def test_raises_conflict_when_the_job_transitions_away_concurrently(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        job = await fake_job_repository.create(
            make_job(created_by_id=owner.id, status=JobStatus.RUNNING)
        )

        async def fake_transition(job_id, *, expected_statuses, new_status, **kwargs):
            # Simulate a concurrent worker completing the job right before
            # our own transition_status call would have taken effect.
            stored_job = await fake_job_repository.get_by_id(job_id)
            stored_job.status = JobStatus.COMPLETED
            return False

        fake_job_repository.transition_status = fake_transition

        with pytest.raises(ValueError, match="Cannot cancel a job with status 'completed'"):
            await job_service.cancel_job(job.id, owner)


class TestGetLogs:
    async def test_owner_can_get_logs(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        job = await fake_job_repository.create(make_job(created_by_id=owner.id))
        await fake_job_repository.add_log(job.id, "hello")

        logs = await job_service.get_logs(job.id, owner)

        assert logs is not None
        assert len(logs) == 1

    async def test_a_non_owner_cannot_get_logs(
        self, job_service, fake_job_repository, make_user, make_job
    ):
        owner = make_user()
        other_user = make_user(email="other@example.com")
        job = await fake_job_repository.create(make_job(created_by_id=owner.id))

        logs = await job_service.get_logs(job.id, other_user)

        assert logs is None

    async def test_returns_none_when_the_job_does_not_exist(self, job_service, make_user):
        logs = await job_service.get_logs(uuid.uuid4(), make_user())

        assert logs is None