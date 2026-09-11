import uuid
from abc import ABC, abstractmethod

from app.models.user import User, UserRole


class IUserRepository(ABC):
    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def get_by_id(self, user_id: uuid.UUID) -> User | None: ...

    @abstractmethod
    async def create(self, user: User) -> User: ...

    @abstractmethod
    async def update_role(self, user_id: uuid.UUID, role: UserRole) -> User | None: ...