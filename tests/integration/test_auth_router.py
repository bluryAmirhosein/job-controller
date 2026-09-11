"""
HTTP-level integration tests for the auth router.

Strategy: AuthService is injected as a mock so these tests focus only on
HTTP-layer behavior (status codes, error mapping, and role-based access);
the business logic itself is covered in test_auth_service.py.
"""
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.auth import router as auth_router
from app.core.dependencies import get_auth_service, get_current_user
from app.models.user import UserRole


@pytest.fixture
def app_with_overrides(make_user):
    app = FastAPI()
    app.include_router(auth_router)

    mock_auth_service = AsyncMock()
    admin_user = make_user(role=UserRole.ADMIN)

    app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
    app.dependency_overrides[get_current_user] = lambda: admin_user

    return app, mock_auth_service, admin_user


@pytest.fixture
def client(app_with_overrides):
    app, mock_auth_service, admin_user = app_with_overrides
    return TestClient(app), mock_auth_service, admin_user


class TestRegisterEndpoint:
    def test_returns_201_on_success(self, client, make_user):
        test_client, mock_auth_service, _ = client
        mock_auth_service.register.return_value = make_user(email="new@example.com")

        response = test_client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "StrongPass1"},
        )

        assert response.status_code == 201
        assert response.json()["email"] == "new@example.com"

    def test_returns_400_when_service_raises_value_error(self, client):
        test_client, mock_auth_service, _ = client
        mock_auth_service.register.side_effect = ValueError(
            "User with this email already exists"
        )

        response = test_client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "StrongPass1"},
        )

        assert response.status_code == 400


class TestLoginEndpoint:
    def test_returns_token_on_success(self, client):
        test_client, mock_auth_service, _ = client
        mock_auth_service.authenticate.return_value = "fake-jwt-token"

        response = test_client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "StrongPass1"},
        )

        assert response.status_code == 200
        assert response.json()["access_token"] == "fake-jwt-token"

    def test_returns_401_on_invalid_credentials(self, client):
        test_client, mock_auth_service, _ = client
        mock_auth_service.authenticate.side_effect = ValueError(
            "Invalid email or password"
        )

        response = test_client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "wrong-password"},
        )

        assert response.status_code == 401


class TestGetMeEndpoint:
    def test_returns_current_authenticated_user(self, client):
        test_client, _, admin_user = client

        response = test_client.get("/auth/me")

        assert response.status_code == 200
        assert response.json()["email"] == admin_user.email


class TestUpdateUserRoleEndpoint:
    def test_admin_can_update_role_successfully(self, client, make_user):
        test_client, mock_auth_service, _ = client
        target_id = uuid.uuid4()
        mock_auth_service.set_user_role.return_value = make_user(
            user_id=target_id, role=UserRole.ADMIN
        )

        response = test_client.patch(
            f"/auth/users/{target_id}/role", json={"role": "admin"}
        )

        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    def test_returns_404_when_target_user_not_found(self, client):
        test_client, mock_auth_service, _ = client
        mock_auth_service.set_user_role.side_effect = ValueError("User not found")

        response = test_client.patch(
            f"/auth/users/{uuid.uuid4()}/role", json={"role": "admin"}
        )

        assert response.status_code == 404

    def test_returns_400_for_other_business_rule_violations(self, client):
        test_client, mock_auth_service, _ = client
        mock_auth_service.set_user_role.side_effect = ValueError(
            "You cannot demote your own account"
        )

        response = test_client.patch(
            f"/auth/users/{uuid.uuid4()}/role", json={"role": "user"}
        )

        assert response.status_code == 400

    def test_returns_403_when_current_user_is_not_admin(self, app_with_overrides, make_user):
        # require_role uses get_current_user, so by overriding the same dependency
        # and returning a non-admin user, we test the actual role-checking flow.
        app, _, _ = app_with_overrides
        regular_user = make_user(role=UserRole.USER)
        app.dependency_overrides[get_current_user] = lambda: regular_user

        test_client = TestClient(app)
        response = test_client.patch(
            f"/auth/users/{uuid.uuid4()}/role", json={"role": "admin"}
        )

        assert response.status_code == 403