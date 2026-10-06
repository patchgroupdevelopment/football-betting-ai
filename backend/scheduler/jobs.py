"""APScheduler wiring (timezone: Asia/Baku by default)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import ScheduleConfig
from backend.scheduler.pipeline import DAILY_JOB_ID, PipelineRunner
from backend.services.runs import has_completed_run
from backend.utils.timeutils import local_now, local_today, parse_hhmm

if TYPE_CHECKING:
    from backend.container import AppContainer

logger = logging.getLogger(__name__)

MISFIRE_GRACE_SECONDS = 3600
CATCH_UP_DELAY_SECONDS = 5


def _cron(value: str, tz: ZoneInfo) -> CronTrigger:
    hour, minute = parse_hhmm(value)
    return CronTrigger(hour=hour, minute=minute, timezone=tz)


def build_scheduler(runner: PipelineRunner, schedule: ScheduleConfig, tz: ZoneInfo) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=tz)
    common = {"misfire_grace_time": MISFIRE_GRACE_SECONDS, "coalesce": True, "max_instances": 1, "replace_existing": True}
    scheduler.add_job(
        runner.run_daily, _cron(schedule.daily_pipeline, tz), id=DAILY_JOB_ID, kwargs={"trigger": "schedule"}, **common
    )
    scheduler.add_job(runner.refresh, _cron(schedule.second_run, tz), id="second_run", **common)
    scheduler.add_job(runner.cleanup_cache, _cron(schedule.cache_cleanup, tz), id="cache_cleanup", **common)
    return scheduler


def should_catch_up(now_local: datetime, daily_time: str, already_ran: bool) -> bool:
    """Run today's pipeline at startup if its scheduled time has passed and it has not run yet."""
    hour, minute = parse_hhmm(daily_time)
    return not already_ran and (now_local.hour, now_local.minute) >= (hour, minute)


def next_daily_run(now_local: datetime, daily_time: str) -> datetime:
    hour, minute = parse_hhmm(daily_time)
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now_local else candidate + timedelta(days=1)


async def schedule_catch_up(scheduler: AsyncIOScheduler, runner: PipelineRunner, container: AppContainer) -> bool:
    already_ran = await asyncio.to_thread(has_completed_run, container.db, local_today(container.tz))
    now = local_now(container.tz)
    if not should_catch_up(now, container.config.schedule.daily_pipeline, already_ran):
        return False
    logger.info("Bu gün üçün yükləmə hələ olmayıb — indi başladılır")
    scheduler.add_job(
        runner.run_daily,
        "date",
        run_date=now + timedelta(seconds=CATCH_UP_DELAY_SECONDS),
        kwargs={"trigger": "catch_up"},
        id="catch_up",
        replace_existing=True,
    )
    return True
