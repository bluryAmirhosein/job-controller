# scripts/create_admin.py
import asyncio
import sys

from app.core.security import hash_password
from app.infrastructure.database import async_session_factory
from app.models.user import User, UserRole
from app.repositories.implementations.user_repository import SQLAlchemyUserRepository


async def main(email: str, password: str) -> None:
    async with async_session_factory() as session:
        repository = SQLAlchemyUserRepository(session)

        existing = await repository.get_by_email(email)
        if existing is not None:
            existing.role = UserRole.ADMIN
            await session.commit()
            print(f"Existing user {email} promoted to admin.")
            return

        user = User(
            email=email,
            hashed_password=hash_password(password),
            role=UserRole.ADMIN,
        )
        await repository.create(user)
        print(f"Admin user {email} created.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.create_admin <email> <password>")
        sys.exit(1)

    asyncio.run(main(sys.argv[1], sys.argv[2]))