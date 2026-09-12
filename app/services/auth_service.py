import asyncio
import uuid

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User, UserRole
from app.repositories.interfaces.user_repository import IUserRepository
from app.schemas.auth import RegisterRequest


class AuthService:
    def __init__(self, user_repository: IUserRepository):
        self._user_repository = user_repository

    async def register(self, data: RegisterRequest) -> User:
        existing_user = await self._user_repository.get_by_email(data.email)
        if existing_user is not None:
            raise ValueError("User with this email already exists")

        # bcrypt hashing is CPU-bound and synchronous - running it directly
        # inside this coroutine blocks the entire event loop for the
        # duration of the hash (tens to hundreds of ms). Offload it to a
        # worker thread so other requests keep being served concurrently.
        hashed_password = await asyncio.to_thread(hash_password, data.password)

        user = User(
            email=data.email,
            hashed_password=hashed_password,
            role=UserRole.USER,
        )
        return await self._user_repository.create(user)

    async def authenticate(self, email: str, password: str) -> str:
        user = await self._user_repository.get_by_email(email)
        if user is None:
            # Still do a dummy hash comparison so response time doesn't leak
            # whether the email exists (timing side-channel), while keeping
            # the CPU-bound work off the event loop.
            await asyncio.to_thread(verify_password, password, hash_password(""))
            raise ValueError("Invalid email or password")

        password_ok = await asyncio.to_thread(verify_password, password, user.hashed_password)
        if not password_ok:
            raise ValueError("Invalid email or password")
        if not user.is_active:
            raise ValueError("User account is disabled")

        return create_access_token(subject=str(user.id), role=user.role.value)

    async def set_user_role(
        self, target_user_id: uuid.UUID, new_role: UserRole, current_user: User
    ) -> User:
        if target_user_id == current_user.id and new_role != UserRole.ADMIN:
            raise ValueError("You cannot demote your own account")

        updated_user = await self._user_repository.update_role(target_user_id, new_role)
        if updated_user is None:
            raise ValueError("User not found")

        return updated_user