"""Telegram users known to the system."""

from __future__ import annotations

from sqlalchemy import select

from backend.database.session import Database
from backend.models import User


def register_user(db: Database, chat_id: int, name: str | None) -> None:
    with db.session() as session:
        user = session.scalar(select(User).where(User.telegram_chat_id == chat_id))
        if user is None:
            session.add(User(telegram_chat_id=chat_id, name=name, is_active=True))
        else:
            user.name = name or user.name
            user.is_active = True
