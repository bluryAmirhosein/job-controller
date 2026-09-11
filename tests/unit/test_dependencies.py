import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.dependencies import get_current_user, require_role
from app.core.security import create_access_token
from app.models.user import UserRole


class TestGetCurrentUser:
    async def test_returns_user_for_valid_token(self, fake_user_repository, make_user):
        user = make_user()
        await fake_user_repository.create(user)
        token = create_access_token(subject=str(user.id), role=user.role.value)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        result = await get_current_user(
            credentials=credentials, user_repository=fake_user_repository
        )

        assert result.id == user.id

    async def test_raises_401_for_malformed_token(self, fake_user_repository):
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="not-a-real-token"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                credentials=credentials, user_repository=fake_user_repository
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_when_user_does_not_exist(self, fake_user_repository):
        token = create_access_token(subject=str(uuid.uuid4()), role="user")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                credentials=credentials, user_repository=fake_user_repository
            )
        assert exc_info.value.status_code == 401

    async def test_raises_401_when_user_is_inactive(self, fake_user_repository, make_user):
        user = make_user(is_active=False)
        await fake_user_repository.create(user)
        token = create_access_token(subject=str(user.id), role=user.role.value)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                credentials=credentials, user_repository=fake_user_repository
            )
        assert exc_info.value.status_code == 401


class TestRequireRole:
    async def test_allows_user_with_permitted_role(self, make_user):
        admin = make_user(role=UserRole.ADMIN)
        role_checker = require_role(UserRole.ADMIN)

        result = await role_checker(current_user=admin)

        assert result is admin

    async def test_rejects_user_without_permitted_role(self, make_user):
        regular_user = make_user(role=UserRole.USER)
        role_checker = require_role(UserRole.ADMIN)

        with pytest.raises(HTTPException) as exc_info:
            await role_checker(current_user=regular_user)
        assert exc_info.value.status_code == 403