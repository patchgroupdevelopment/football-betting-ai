"""System status snapshot for /status, the CLI and the API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models import Match, OddsSnapshot, Team
from backend.services.errors import ProviderError
from backend.services.runs import get_latest_run
from backend.utils.timeutils import to_local

if TYPE_CHECKING:
    from backend.container import AppContainer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaInfo:
    plan: str | None
    used: int | None
    limit: int | None
    remaining: int | None


@dataclass(frozen=True)
class SystemStatus:
    paper_mode: bool
    last_run_date: date | None
    last_run_status: str | None
    last_run_finished_local: datetime | None
    quota: QuotaInfo | None
    quota_error: str | None
    matches: int
    teams: int
    odds: int
    telegram_enabled: bool
    llm_enabled: bool
    next_run_local: datetime | None


def collect_status(container: AppContainer, next_run_local: datetime | None = None) -> SystemStatus:
    quota: QuotaInfo | None = None
    quota_error: str | None = None
    try:
        account = container.api_client().account_status()
        remaining = None
        if account.requests_limit_day is not None and account.requests_current is not None:
            remaining = max(0, account.requests_limit_day - account.requests_current)
        quota = QuotaInfo(account.plan, account.requests_current, account.requests_limit_day, remaining)
    except ProviderError as exc:
        logger.info("API vəziyyəti alınmadı: %s", exc.detail or exc)
        quota_error = exc.user_message

    with container.db.session() as session:
        run = get_latest_run(session)
        matches = session.scalar(select(func.count(Match.id))) or 0
        teams = session.scalar(select(func.count(Team.id))) or 0
        odds = session.scalar(select(func.count(OddsSnapshot.id))) or 0
        last_date = run.run_date if run else None
        last_status = run.status if run else None
        finished = to_local(run.finished_at, container.tz) if run and run.finished_at else None

    return SystemStatus(
        paper_mode=container.settings.paper_mode,
        last_run_date=last_date,
        last_run_status=last_status,
        last_run_finished_local=finished,
        quota=quota,
        quota_error=quota_error,
        matches=matches,
        teams=teams,
        odds=odds,
        telegram_enabled=container.settings.telegram_configured,
        llm_enabled=container.settings.llm_enabled,
        next_run_local=next_run_local,
    )
