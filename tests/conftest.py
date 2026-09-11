"""
Shared fixtures for all tests.

Important note: It is assumed that the User model has the following fields:
    id, email, hashed_password, role, is_active
If the field names differ in the actual project, only this file needs to be updated;
the rest of the tests will work without any changes.
"""
import uuid

import pytest

from app.models.user import User, UserRole
from app.repositories.interfaces.user_repository import IUserRepository


class FakeUserRepository(IUserRepository):
    """
    An in-memory implementation of IUserRepository.
    Instead of mocking each method individually, it simulates the actual behavior
    of a repository so that service tests do not depend on SQLAlchemy implementation details.
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


@pytest.fixture
def fake_user_repository() -> FakeUserRepository:
    return FakeUserRepository()


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