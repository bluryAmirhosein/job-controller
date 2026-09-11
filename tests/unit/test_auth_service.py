import uuid

import pytest

from app.core.security import hash_password, verify_password
from app.models.user import UserRole
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService


@pytest.fixture
def auth_service(fake_user_repository):
    return AuthService(fake_user_repository)


class TestRegister:
    async def test_register_creates_user_with_hashed_password_and_default_role(
        self, auth_service
    ):
        payload = RegisterRequest(email="new@example.com", password="StrongPass1")

        user = await auth_service.register(payload)

        assert user.email == "new@example.com"
        assert user.role == UserRole.USER
        assert user.hashed_password != "StrongPass1"
        assert verify_password("StrongPass1", user.hashed_password) is True

    async def test_register_raises_when_email_already_exists(
        self, auth_service, fake_user_repository, make_user
    ):
        await fake_user_repository.create(make_user(email="dup@example.com"))
        payload = RegisterRequest(email="dup@example.com", password="StrongPass1")

        with pytest.raises(ValueError, match="already exists"):
            await auth_service.register(payload)


class TestAuthenticate:
    async def test_authenticate_returns_token_for_valid_credentials(self, auth_service):
        await auth_service.register(
            RegisterRequest(email="ok@example.com", password="StrongPass1")
        )

        token = await auth_service.authenticate("ok@example.com", "StrongPass1")

        assert isinstance(token, str) and token != ""

    async def test_authenticate_raises_when_user_does_not_exist(self, auth_service):
        with pytest.raises(ValueError, match="Invalid email or password"):
            await auth_service.authenticate("missing@example.com", "whatever")

    async def test_authenticate_raises_when_password_is_wrong(self, auth_service):
        await auth_service.register(
            RegisterRequest(email="ok2@example.com", password="StrongPass1")
        )

        with pytest.raises(ValueError, match="Invalid email or password"):
            await auth_service.authenticate("ok2@example.com", "wrong-password")

    async def test_authenticate_raises_when_user_is_inactive(
        self, auth_service, fake_user_repository, make_user
    ):
        inactive_user = make_user(
            email="inactive@example.com",
            hashed_password=hash_password("StrongPass1"),
            is_active=False,
        )
        await fake_user_repository.create(inactive_user)

        with pytest.raises(ValueError, match="disabled"):
            await auth_service.authenticate("inactive@example.com", "StrongPass1")


class TestSetUserRole:
    async def test_admin_can_change_role_of_another_user(
        self, auth_service, fake_user_repository, make_user
    ):
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        target = make_user(email="target@example.com", role=UserRole.USER)
        await fake_user_repository.create(admin)
        await fake_user_repository.create(target)

        updated = await auth_service.set_user_role(target.id, UserRole.ADMIN, admin)

        assert updated.role == UserRole.ADMIN

    async def test_raises_when_target_user_not_found(
        self, auth_service, fake_user_repository, make_user
    ):
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        await fake_user_repository.create(admin)

        with pytest.raises(ValueError, match="User not found"):
            await auth_service.set_user_role(uuid.uuid4(), UserRole.ADMIN, admin)

    async def test_admin_cannot_demote_own_account(
        self, auth_service, fake_user_repository, make_user
    ):
        # This test covers a critical business rule: an admin must not be able to
        # accidentally remove their own admin access.
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        await fake_user_repository.create(admin)

        with pytest.raises(ValueError, match="cannot demote your own account"):
            await auth_service.set_user_role(admin.id, UserRole.USER, admin)

    async def test_admin_can_reassign_own_role_to_admin(
        self, auth_service, fake_user_repository, make_user
    ):
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        await fake_user_repository.create(admin)

        updated = await auth_service.set_user_role(admin.id, UserRole.ADMIN, admin)

        assert updated.role == UserRole.ADMIN