"""One-shot Telegram polling for scheduled environments (GitHub Actions).

There is no long-running process there: a job fetches the updates that queued
up since the last run, answers them and confirms them to Telegram. Updates are
confirmed *before* they are processed, so a crash never makes the bot answer
(or refresh) twice — at worst one message goes unanswered.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Bot
from telegram.error import TelegramError

from backend.i18n import t
from backend.presenters.formatters import text_message
from backend.telegram.commands import REFRESH, Incoming, authorization_reply, parse_command, respond
from backend.telegram.notifier import TelegramNotifier

if TYPE_CHECKING:
    from backend.container import AppContainer
    from backend.scheduler.pipeline import PipelineRunner

logger = logging.getLogger(__name__)


async def poll_once(container: AppContainer, runner: PipelineRunner, bot: Bot, notifier: TelegramNotifier) -> int:
    """Answer all pending messages; returns how many were handled."""
    updates = await bot.get_updates(timeout=0, allowed_updates=["message"])
    if not updates:
        return 0
    await bot.get_updates(offset=updates[-1].update_id + 1, timeout=0)  # confirm first: at most once

    handled = 0
    for update in updates:
        chat, message = update.effective_chat, update.effective_message
        if chat is None or message is None or not message.text:
            continue
        try:
            command, _ = parse_command(message.text)
            if command == REFRESH and authorization_reply(container, chat.id) is None:
                await notifier.send_to(chat.id, text_message(t("bot.refresh_started")))
                await runner.run_daily(trigger="manual")  # sends its own reports
            else:
                user = update.effective_user
                incoming = Incoming(chat.id, user.full_name if user else None, message.text)
                await notifier.send_to(chat.id, await respond(container, runner, incoming))
            handled += 1
        except TelegramError:
            logger.exception("Telegram cavabı göndərilmədi")
        except Exception:
            logger.exception("Telegram əmri emal edilərkən xəta: %s", message.text)
            await notifier.send_to(chat.id, text_message(t("error.unexpected")))
    return handled
