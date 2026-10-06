"""Runs the daily pipeline (ingestion → settlement → analysis) off the event loop and delivers its reports.

One run at a time: a scheduled run, a catch-up run and a manual /yenile cannot
overlap. The scheduled daily analysis is de-duplicated per day, so a catch-up
run and the regular 08:00 run never both send it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from backend.presenters.formatters import format_daily_analysis, format_ingestion_report, text_message
from backend.presenters.messages import MessageBuilder
from backend.presenters.results import format_settlement
from backend.services.analysis import AnalysisReport
from backend.services.errors import ProviderError
from backend.services.ingestion import IngestionReport
from backend.services.notifications import mark_sent, was_sent
from backend.services.picks import build_daily_analysis
from backend.services.results import SettlementReport, track_record
from backend.utils.timeutils import local_today

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from backend.container import AppContainer
    from backend.telegram.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

Trigger = Literal["schedule", "catch_up", "manual", "refresh"]
DAILY_JOB_ID = "daily_pipeline"


@dataclass(frozen=True)
class PipelineResult:
    ingestion: IngestionReport
    analysis: AnalysisReport | None
    settlement: SettlementReport | None = None


class PipelineRunner:
    def __init__(self, container: AppContainer, notifier: TelegramNotifier | None = None) -> None:
        self._container = container
        self._lock = asyncio.Lock()
        self.notifier = notifier
        self.scheduler: AsyncIOScheduler | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def next_daily_run(self) -> datetime | None:
        if self.scheduler is None:
            return None
        job = self.scheduler.get_job(DAILY_JOB_ID)
        return job.next_run_time if job is not None else None

    async def run_daily(self, target_date: date | None = None, *, trigger: Trigger = "schedule") -> PipelineResult | None:
        if self._lock.locked():
            logger.info("Pipeline artıq gedir — yeni başlatma (%s) buraxıldı", trigger)
            return None
        async with self._lock:
            day = target_date or local_today(self._container.tz)
            logger.info("Gündəlik pipeline başladı: %s (%s)", day, trigger)
            try:
                ingestion = await asyncio.to_thread(self._container.run_ingestion, day)
            except ProviderError as exc:  # e.g. FOOTBALL_API_KEY missing
                logger.error("Pipeline başlamadı: %s", exc.user_message)
                await self._send(text_message(exc.user_message))
                return None
            settlement: SettlementReport | None = None
            try:  # yesterday's results arrived with the ingestion: settle before analysing the new day
                settlement = await asyncio.to_thread(self._container.run_settlement)
            except Exception:
                logger.exception("Nəticələrin hesablanmasında xəta")
            analysis: AnalysisReport | None = None
            try:
                analysis = await asyncio.to_thread(self._container.run_analysis, day)
                await asyncio.to_thread(self._container.open_pyramid_stage, day)
            except Exception:
                logger.exception("Analiz mərhələsində xəta")
            result = PipelineResult(ingestion, analysis, settlement)
            await self._deliver_results(settlement)
            await self._deliver(result, day, trigger)
            return result

    async def refresh(self) -> PipelineResult | None:
        """Midday re-run: fresher odds and injuries; the cache keeps unchanged data from being re-downloaded."""
        return await self.run_daily(trigger="refresh")

    async def cleanup_cache(self) -> None:
        removed = await asyncio.to_thread(self._container.cache.purge_expired)
        logger.info("Köhnə cache qeydləri silindi: %d", removed)

    async def daily_analysis_message(self, day: date) -> MessageBuilder:
        container = self._container
        analysis = await asyncio.to_thread(build_daily_analysis, container.db, day, container.tz)
        state = await asyncio.to_thread(container.pyramid_service().get_state)
        return format_daily_analysis(analysis, state, paper_mode=container.settings.paper_mode)

    async def _deliver(self, result: PipelineResult, day: date, trigger: Trigger) -> None:
        if self.notifier is None or trigger == "refresh":
            return
        if trigger == "manual":
            await self._send(format_ingestion_report(result.ingestion))

        dedup_key = f"daily_analysis:{day.isoformat()}"
        if trigger != "manual" and await asyncio.to_thread(was_sent, self._container.db, dedup_key):
            return
        if await self._send(await self.daily_analysis_message(day)):
            await asyncio.to_thread(mark_sent, self._container.db, dedup_key, "daily_analysis")

    async def _deliver_results(self, settlement: SettlementReport | None) -> None:
        """Every settled pick is reported once: a bet is settled only once."""
        if settlement is None or not settlement.settled:
            return
        record = await asyncio.to_thread(track_record, self._container.db)
        await self._send(format_settlement(settlement, record))

    async def _send(self, message: MessageBuilder) -> bool:
        if self.notifier is None:
            return False
        return await self.notifier.send(message)
