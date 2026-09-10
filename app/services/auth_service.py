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

        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            role=UserRole.USER,
        )
        return await self._user_repository.create(user)

    async def authenticate(self, email: str, password: str) -> str:
        user = await self._user_repository.get_by_email(email)
        if user is None or not verify_password(password, user.hashed_password):
            raise ValueError("Invalid email or password")
        if not user.is_active:
            raise ValueError("User account is disabled")

        return create_access_token(subject=str(user.id), role=user.role.value)