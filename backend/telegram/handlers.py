"""Telegram long-polling handlers (local / always-on mode). All replies are Azerbaijani.

The command logic itself lives in ``backend.telegram.commands`` and is shared
with the GitHub Actions poller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from backend.i18n import t
from backend.presenters.formatters import text_message
from backend.presenters.messages import MessageBuilder, split_message
from backend.telegram.commands import REFRESH, Incoming, authorization_reply, parse_command, respond
from backend.telegram.notifier import NO_PREVIEW

if TYPE_CHECKING:
    from backend.container import AppContainer
    from backend.scheduler.pipeline import PipelineRunner

logger = logging.getLogger(__name__)


def _container(context: ContextTypes.DEFAULT_TYPE) -> AppContainer:
    return context.application.bot_data["container"]


def _runner(context: ContextTypes.DEFAULT_TYPE) -> PipelineRunner:
    return context.application.bot_data["runner"]


async def _reply(update: Update, message: MessageBuilder) -> None:
    if update.effective_message is None:
        return
    for chunk in split_message(message.render_html()):
        await update.effective_message.reply_text(chunk, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    container, runner = _container(context), _runner(context)
    command, _ = parse_command(message.text)
    if command == REFRESH and authorization_reply(container, chat.id) is None:
        if runner.busy:
            await _reply(update, text_message(t("bot.refresh_busy")))
            return
        await _reply(update, text_message(t("bot.refresh_started")))
        # Runs in the background; the runner sends the reports when done.
        context.application.create_task(runner.run_daily(trigger="manual"), update=update)
        return
    user = update.effective_user
    reply = await respond(container, runner, Incoming(chat.id, user.full_name if user else None, message.text or ""))
    await _reply(update, reply)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Telegram əmri emal edilərkən xəta", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await _reply(update, text_message(t("error.unexpected")))
        except TelegramError:
            pass


def register(application: Application) -> None:
    application.add_handler(MessageHandler(filters.COMMAND | filters.TEXT, on_message))
    application.add_error_handler(on_error)
