"""Application entry point: ``python -m backend.main``.

One process, one asyncio event loop, hosting:
- the web API (FastAPI + uvicorn),
- the scheduler (APScheduler) that runs the daily pipeline,
- the Telegram bot (long polling).
Blocking work (HTTP to data providers, SQLite writes) runs in worker threads.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from backend.api.routes import register_routes
from backend.config import PROJECT_ROOT, ConfigError, get_config, get_settings
from backend.container import AppContainer, build_container
from backend.database.migrations import upgrade_to_head
from backend.i18n import t
from backend.scheduler.jobs import build_scheduler, schedule_catch_up
from backend.scheduler.pipeline import PipelineRunner
from backend.telegram.bot import TelegramBot
from backend.utils.logging import configure_logging, force_utf8_stdio

logger = logging.getLogger(__name__)


async def _start_bot(container: AppContainer, runner: PipelineRunner) -> TelegramBot | None:
    if not container.settings.telegram_configured:
        logger.warning("Telegram botu deaktivdir (TELEGRAM_ENABLED=false və ya TELEGRAM_BOT_TOKEN boşdur)")
        return None
    bot = TelegramBot(container, runner)
    try:
        await bot.start()
    except Exception:
        logger.exception(t("error.telegram_start"))
        with contextlib.suppress(Exception):
            await bot.stop()
        return None
    runner.notifier = bot.notifier
    if not container.settings.telegram_chat_ids:
        logger.warning(
            "TELEGRAM_CHAT_ID təyin edilməyib — bot quraşdırma rejimindədir: bota /start yazın, "
            "chat ID-nizi göndərəcək"
        )
    logger.info("Telegram botu işə düşdü")
    return bot


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: AppContainer = app.state.container
    runner = PipelineRunner(container)
    app.state.runner = runner
    bot = await _start_bot(container, runner)

    scheduler = build_scheduler(runner, container.config.schedule, container.tz)
    scheduler.start()
    runner.scheduler = scheduler
    if container.config.schedule.catch_up_on_start:
        await schedule_catch_up(scheduler, runner, container)

    logger.info(
        "Sistem işə düşdü. Vaxt zonası: %s, rejim: %s, növbəti yükləmə: %s",
        container.settings.timezone,
        "simulyasiya" if container.settings.paper_mode else "real",
        runner.next_daily_run(),
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        if bot is not None:
            await bot.stop()
        container.close()
        logger.info("Sistem dayandırıldı")


def create_app(container: AppContainer | None = None, *, start_background: bool = True) -> FastAPI:
    """``start_background=False`` skips the bot and scheduler (used by tests)."""
    app = FastAPI(
        title=t("web.title"),
        lifespan=_lifespan if start_background else None,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.container = container or build_container()
    register_routes(app)
    return app


def main() -> None:
    force_utf8_stdio()
    try:
        settings = get_settings()
        config = get_config()
    except ConfigError as exc:
        print(exc.user_message)
        raise SystemExit(1) from None

    configure_logging(settings.log_level, PROJECT_ROOT / "logs")
    try:
        upgrade_to_head(settings.database_url)
    except Exception:
        logger.exception(t("error.database"))
        raise SystemExit(1) from None

    app = create_app(build_container(settings, config))
    logger.info("Veb interfeys: http://%s:%s", settings.web_host, settings.web_port)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_config=None)


if __name__ == "__main__":
    main()
