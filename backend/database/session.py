"""Engine and session management.

SQLite today, PostgreSQL later: only ``DATABASE_URL`` changes. Services open a
short unit of work with ``with db.session() as s:`` — it commits on success and
rolls back on error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def is_memory_sqlite(url: str) -> bool:
    return url in ("sqlite://", "sqlite:///:memory:") or "mode=memory" in url


def ensure_sqlite_parent_dir(url: str) -> None:
    if not url.startswith("sqlite") or is_memory_sqlite(url):
        return
    database = make_url(url).database
    if database:
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _sqlite_pragmas(dbapi_connection: Any, _record: Any, *, wal: bool) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    if wal:
        # WAL lets the Telegram bot read while the ingestion thread writes.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        is_sqlite = url.startswith("sqlite")
        kwargs: dict[str, Any] = {"echo": echo}
        if is_sqlite:
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if is_memory_sqlite(url):
                kwargs["poolclass"] = StaticPool
            else:
                ensure_sqlite_parent_dir(url)
        self.engine: Engine = create_engine(url, **kwargs)
        if is_sqlite:
            event.listen(self.engine, "connect", partial(_sqlite_pragmas, wal=not is_memory_sqlite(url)))
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def create_all(self) -> None:
        """Create tables directly (tests). Real databases use Alembic migrations."""
        from backend.database.base import Base
        import backend.models  # noqa: F401  (registers all tables)

        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()
