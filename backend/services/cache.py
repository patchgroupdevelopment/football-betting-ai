"""TTL cache for raw API responses, persisted in the database.

Surviving restarts matters on a 100-requests/day plan: restarting the app must
not re-download what was fetched an hour ago.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete

from backend.database.session import Database
from backend.models import ApiCacheEntry
from backend.utils.timeutils import utcnow

logger = logging.getLogger(__name__)


class ResponseCache:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._db = db
        self._clock = clock

    @staticmethod
    def make_key(endpoint: str, params: Mapping[str, Any]) -> str:
        raw = json.dumps({"e": endpoint, "p": dict(params)}, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, endpoint: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
        key = self.make_key(endpoint, params)
        with self._db.session() as session:
            entry = session.get(ApiCacheEntry, key)
            if entry is None:
                return None
            if entry.expires_at is not None and entry.expires_at <= self._clock():
                return None
            return entry.payload

    def set(self, endpoint: str, params: Mapping[str, Any], payload: dict[str, Any], ttl_seconds: int) -> None:
        """Best effort: a cache write failure must never break data ingestion."""
        key = self.make_key(endpoint, params)
        now = self._clock()
        try:
            with self._db.session() as session:
                entry = session.get(ApiCacheEntry, key)
                if entry is None:
                    entry = ApiCacheEntry(key=key)
                    session.add(entry)
                entry.endpoint = endpoint
                entry.params = dict(params)
                entry.payload = payload
                entry.fetched_at = now
                entry.expires_at = now + timedelta(seconds=ttl_seconds)
        except Exception:  # noqa: BLE001 — cache is optional
            logger.warning("Cache yazılmadı: %s", endpoint, exc_info=True)

    def purge_expired(self) -> int:
        with self._db.session() as session:
            result = session.execute(
                delete(ApiCacheEntry).where(
                    ApiCacheEntry.expires_at.is_not(None), ApiCacheEntry.expires_at <= self._clock()
                )
            )
            return result.rowcount or 0
