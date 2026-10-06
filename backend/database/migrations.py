"""Programmatic Alembic upgrade, run at startup and by ``cli init-db``."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from backend.config import PROJECT_ROOT
from backend.database.session import ensure_sqlite_parent_dir


def upgrade_to_head(database_url: str) -> None:
    ensure_sqlite_parent_dir(database_url)
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # ConfigParser treats "%" as interpolation syntax.
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    # Keep the application's logging setup; alembic.ini would otherwise replace it.
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
