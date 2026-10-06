"""Queries over pipeline run records (``model_runs``)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.session import Database
from backend.models import ModelRun
from backend.models.constants import RunStatus

RUN_KIND_DAILY = "daily_ingestion"
RUN_KIND_ANALYSIS = "daily_analysis"


def get_latest_run(session: Session, day: date | None = None, kind: str = RUN_KIND_DAILY) -> ModelRun | None:
    stmt = select(ModelRun).where(ModelRun.kind == kind)
    if day is not None:
        stmt = stmt.where(ModelRun.run_date == day)
    return session.scalar(stmt.order_by(ModelRun.id.desc()).limit(1))


def has_completed_run(db: Database, day: date) -> bool:
    with db.session() as session:
        run = get_latest_run(session, day)
        return run is not None and run.status in (RunStatus.SUCCESS, RunStatus.PARTIAL)
