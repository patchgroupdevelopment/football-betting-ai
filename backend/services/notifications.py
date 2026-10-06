"""Record of sent notifications, so scheduled reports are not sent twice."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.database.session import Database
from backend.models import Notification

logger = logging.getLogger(__name__)


def was_sent(db: Database, dedup_key: str) -> bool:
    with db.session() as session:
        return session.scalar(select(Notification.id).where(Notification.dedup_key == dedup_key)) is not None


def mark_sent(db: Database, dedup_key: str, kind: str, *, preview: str = "", chat_id: int | None = None) -> None:
    try:
        with db.session() as session:
            session.add(Notification(kind=kind, dedup_key=dedup_key, chat_id=chat_id, preview=preview[:255]))
    except IntegrityError:
        logger.debug("Bildiriş artıq qeyd olunub: %s", dedup_key)
