"""Telegram bot lifecycle, embedded in the application's asyncio event loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import BotCommand
from telegram.ext import Application

from backend.i18n import t
from backend.presenters.formatters import BOT_COMMANDS
from backend.telegram import handlers
from backend.telegram.notifier import TelegramNotifier

if TYPE_CHECKING:
    from backend.container import AppContainer
    from backend.scheduler.pipeline import PipelineRunner

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, container: AppContainer, runner: PipelineRunner) -> None:
        token = container.settings.telegram_bot_token.get_secret_value()
        self.application = Application.builder().token(token).build()
        self.application.bot_data.update(container=container, runner=runner)
        handlers.register(self.application)
        self.notifier = TelegramNotifier(self.application.bot, container.settings.telegram_chat_ids)

    async def start(self) -> None:
        app = self.application
        await app.initialize()
        await app.bot.set_my_commands([BotCommand(command, t(key)) for command, key in BOT_COMMANDS])
        await app.start()
        if app.updater is not None:
            await app.updater.start_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        app = self.application
        if app.updater is not None and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
