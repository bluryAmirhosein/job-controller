import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.infrastructure.database import get_db_session
from app.models.user import User, UserRole
from app.repositories.implementations.job_repository import SQLAlchemyJobRepository
from app.repositories.implementations.user_repository import SQLAlchemyUserRepository
from app.repositories.interfaces.job_repository import IJobRepository
from app.repositories.interfaces.user_repository import IUserRepository
from app.services.auth_service import AuthService
from app.services.job_service import JobService

bearer_scheme = HTTPBearer(auto_error=True)


def get_user_repository(session: AsyncSession = Depends(get_db_session)) -> IUserRepository:
    return SQLAlchemyUserRepository(session)


def get_auth_service(user_repository: IUserRepository = Depends(get_user_repository)) -> AuthService:
    return AuthService(user_repository)


def get_job_repository(session: AsyncSession = Depends(get_db_session)) -> IJobRepository:
    return SQLAlchemyJobRepository(session)


def get_job_service(job_repository: IJobRepository = Depends(get_job_repository)) -> JobService:
    return JobService(job_repository)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    user_repository: IUserRepository = Depends(get_user_repository),
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user = await user_repository.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return user


def require_role(*allowed_roles: UserRole) -> Callable:
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user

    return role_checker