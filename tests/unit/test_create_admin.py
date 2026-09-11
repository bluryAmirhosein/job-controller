"""
Tests for scripts/create_admin.py

Assumptions:
1. The scripts directory is a Python package (scripts/__init__.py exists),
   so the import `from scripts.create_admin import main` works.
2. Only the async `main` function is tested; the
   `if __name__ == "__main__":` block (CLI argument parsing) is intentionally
   not tested because it does not make much sense to test separately from
   sys.exit/print. If you want that part to be more testable, I recommend
   extracting argument parsing into a separate function (e.g., parse_args).
"""
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.user import User, UserRole
from scripts.create_admin import main


@pytest.fixture
def fake_session():
    session = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def fake_session_factory(fake_session):
    """
    Simulates async_session_factory: a callable that returns an async context
    manager when called, exactly like the real usage:
    `async with async_session_factory() as session:`.
    """

    @asynccontextmanager
    async def _factory():
        yield fake_session

    return _factory


class TestCreateAdminMain:
    async def test_creates_new_admin_when_user_does_not_exist(
        self, fake_session_factory, capsys
    ):
        with patch(
            "scripts.create_admin.async_session_factory", fake_session_factory
        ), patch("scripts.create_admin.SQLAlchemyUserRepository") as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_email = AsyncMock(return_value=None)
            mock_repo.create = AsyncMock()

            await main("new-admin@example.com", "StrongPass1")

            mock_repo.get_by_email.assert_awaited_once_with("new-admin@example.com")
            mock_repo.create.assert_awaited_once()

            created_user: User = mock_repo.create.await_args.args[0]
            assert created_user.email == "new-admin@example.com"
            assert created_user.role == UserRole.ADMIN
            # The password must be hashed, not stored as plain text.
            assert created_user.hashed_password != "StrongPass1"

            assert "created" in capsys.readouterr().out.lower()

    async def test_promotes_existing_user_to_admin_without_creating_new_one(
        self, fake_session, fake_session_factory, capsys
    ):
        existing_user = User(
            id=uuid.uuid4(),
            email="existing@example.com",
            hashed_password="already-hashed",
            role=UserRole.USER,
        )

        with patch(
            "scripts.create_admin.async_session_factory", fake_session_factory
        ), patch("scripts.create_admin.SQLAlchemyUserRepository") as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_email = AsyncMock(return_value=existing_user)
            mock_repo.create = AsyncMock()

            await main("existing@example.com", "irrelevant-password")

            # An existing user should only be promoted, not recreated.
            assert existing_user.role == UserRole.ADMIN
            fake_session.commit.assert_awaited_once()
            mock_repo.create.assert_not_awaited()

            assert "promoted" in capsys.readouterr().out.lower()

    async def test_does_not_touch_password_when_promoting_existing_user(
        self, fake_session, fake_session_factory
    ):
        # Important security note: promoting an existing user must not replace their
        # current password with the new password provided via the command line.
        existing_user = User(
            id=uuid.uuid4(),
            email="existing@example.com",
            hashed_password="already-hashed",
            role=UserRole.USER,
        )

        with patch(
            "scripts.create_admin.async_session_factory", fake_session_factory
        ), patch("scripts.create_admin.SQLAlchemyUserRepository") as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_email = AsyncMock(return_value=existing_user)
            mock_repo.create = AsyncMock()

            await main("existing@example.com", "some-new-password")

            assert existing_user.hashed_password == "already-hashed"