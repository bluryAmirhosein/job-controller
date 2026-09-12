import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import jobs as job_router_module
from app.api.v1.jobs import router as job_router
from app.core.dependencies import get_current_user, get_job_service
from app.infrastructure.redis_client import get_redis_client
from app.models.job import JobStatus
from app.schemas.job import JobListResponse, JobResponse


@pytest.fixture
def fake_redis_client():
    redis_client = AsyncMock()
    # Comfortably under any rate limit used in these tests, so create_job
    # tests aren't accidentally blocked unless a test overrides this.
    redis_client.eval = AsyncMock(return_value=[1, 60])
    return redis_client


@pytest.fixture
def app_with_overrides(make_user, fake_redis_client):
    app = FastAPI()
    app.include_router(job_router)

    mock_job_service = AsyncMock()
    current_user = make_user()

    app.dependency_overrides[get_job_service] = lambda: mock_job_service
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_redis_client] = lambda: fake_redis_client

    return app, mock_job_service, current_user


@pytest.fixture
def client(app_with_overrides):
    app, mock_job_service, current_user = app_with_overrides
    return TestClient(app), mock_job_service, current_user


class TestCreateJobEndpoint:
    def test_returns_201_when_a_new_job_is_created(self, client, make_job):
        test_client, mock_job_service, current_user = client
        new_job = make_job(created_by_id=current_user.id)
        mock_job_service.create_job.return_value = (new_job, True)

        response = test_client.post(
            "/jobs", json={"task_type": "demo_sleep", "payload": {"seconds": 1}}
        )

        assert response.status_code == 201

    def test_returns_200_when_an_existing_job_is_returned_via_idempotency(
        self, client, make_job
    ):
        test_client, mock_job_service, current_user = client
        existing_job = make_job(created_by_id=current_user.id)
        mock_job_service.create_job.return_value = (existing_job, False)

        response = test_client.post(
            "/jobs",
            json={"task_type": "demo_sleep", "payload": {"seconds": 1}},
            headers={"Idempotency-Key": "same-key"},
        )

        assert response.status_code == 200

    def test_returns_502_when_the_service_fails_to_enqueue(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.create_job.side_effect = RuntimeError("Failed to enqueue job")

        response = test_client.post(
            "/jobs", json={"task_type": "demo_sleep", "payload": {"seconds": 1}}
        )

        assert response.status_code == 502

    def test_returns_429_when_the_rate_limit_is_exceeded(self, client, fake_redis_client):
        test_client, _, _ = client
        fake_redis_client.eval = AsyncMock(return_value=[11, 30])

        response = test_client.post(
            "/jobs", json={"task_type": "demo_sleep", "payload": {"seconds": 1}}
        )

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "30"


class TestListJobsEndpoint:
    def test_returns_jobs_from_the_service_on_a_cache_miss(self, client, make_job):
        test_client, mock_job_service, current_user = client
        job = make_job(created_by_id=current_user.id)
        mock_job_service.list_jobs.return_value = ([job], None)

        with patch.object(
            job_router_module, "get_job_list_version", AsyncMock(return_value=1)
        ), patch.object(
            job_router_module, "get_cached_job_list", AsyncMock(return_value=None)
        ), patch.object(
            job_router_module, "set_cached_job_list", AsyncMock()
        ) as set_cache_mock:
            response = test_client.get("/jobs")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        set_cache_mock.assert_awaited_once()
        mock_job_service.list_jobs.assert_awaited_once()

    def test_returns_a_cached_response_without_calling_the_service(self, client, make_job):
        test_client, mock_job_service, current_user = client
        job = make_job(created_by_id=current_user.id)
        cached_payload = JobListResponse(
            items=[JobResponse.model_validate(job)], next_cursor=None
        )
        cached_json = json.dumps(cached_payload.model_dump(mode="json"))

        with patch.object(
            job_router_module, "get_job_list_version", AsyncMock(return_value=1)
        ), patch.object(
            job_router_module, "get_cached_job_list", AsyncMock(return_value=cached_json)
        ):
            response = test_client.get("/jobs")

        assert response.status_code == 200
        mock_job_service.list_jobs.assert_not_awaited()

    def test_returns_400_for_an_invalid_cursor(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.list_jobs.side_effect = ValueError("Invalid cursor")

        with patch.object(
            job_router_module, "get_job_list_version", AsyncMock(return_value=1)
        ), patch.object(
            job_router_module, "get_cached_job_list", AsyncMock(return_value=None)
        ), patch.object(job_router_module, "set_cached_job_list", AsyncMock()):
            response = test_client.get("/jobs", params={"cursor": "garbage"})

        assert response.status_code == 400


class TestGetJobEndpoint:
    def test_returns_the_job_when_found(self, client, make_job):
        test_client, mock_job_service, current_user = client
        job = make_job(created_by_id=current_user.id)
        mock_job_service.get_job.return_value = job

        response = test_client.get(f"/jobs/{job.id}")

        assert response.status_code == 200

    def test_returns_404_when_not_found(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.get_job.return_value = None

        response = test_client.get(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 404


class TestCancelJobEndpoint:
    def test_returns_the_cancelled_job_on_success(self, client, make_job):
        test_client, mock_job_service, current_user = client
        job = make_job(created_by_id=current_user.id, status=JobStatus.CANCELLED)
        mock_job_service.cancel_job.return_value = job

        response = test_client.post(f"/jobs/{job.id}/cancel")

        assert response.status_code == 200

    def test_returns_404_when_the_job_does_not_exist(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.cancel_job.return_value = None

        response = test_client.post(f"/jobs/{uuid.uuid4()}/cancel")

        assert response.status_code == 404

    def test_returns_409_when_the_job_cannot_be_cancelled(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.cancel_job.side_effect = ValueError(
            "Cannot cancel a job with status 'completed'"
        )

        response = test_client.post(f"/jobs/{uuid.uuid4()}/cancel")

        assert response.status_code == 409


class TestGetJobLogsEndpoint:
    def test_returns_logs_when_the_job_exists(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.get_logs.return_value = []

        response = test_client.get(f"/jobs/{uuid.uuid4()}/logs")

        assert response.status_code == 200

    def test_returns_404_when_the_job_does_not_exist(self, client):
        test_client, mock_job_service, _ = client
        mock_job_service.get_logs.return_value = None

        response = test_client.get(f"/jobs/{uuid.uuid4()}/logs")

        assert response.status_code == 404