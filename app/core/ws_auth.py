"""
WebSocket auth adapter.

Browsers can't set a custom Authorization header on the WebSocket
handshake, so the token is passed as a query param (?token=...).
"""
import uuid

from app.core.security import decode_access_token
from app.infrastructure.database import get_session_context
from app.models.user import User
from app.repositories.implementations.user_repository import SQLAlchemyUserRepository


async def authenticate_websocket(token: str) -> User | None:
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except Exception:
        return None

    async with get_session_context() as session:
        user_repository = SQLAlchemyUserRepository(session)
        user = await user_repository.get_by_id(user_id)

    if user is None or not getattr(user, "is_active", True):
        return None
    return user