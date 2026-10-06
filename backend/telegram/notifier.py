"""Outgoing Telegram messages (reports and alerts)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import TelegramError

from backend.presenters.messages import MessageBuilder, split_message

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class TelegramNotifier:
    def __init__(self, bot: Bot, chat_ids: Sequence[int]) -> None:
        self._bot = bot
        self._chat_ids = list(chat_ids)

    @property
    def enabled(self) -> bool:
        return bool(self._chat_ids)

    async def send(self, message: MessageBuilder) -> bool:
        """Send to every configured chat; long messages are split. Never raises."""
        if not self._chat_ids:
            logger.warning("Telegram mesajı göndərilmədi: TELEGRAM_CHAT_ID təyin edilməyib")
            return False
        results = [await self.send_to(chat_id, message) for chat_id in self._chat_ids]
        return all(results)

    async def send_to(self, chat_id: int, message: MessageBuilder) -> bool:
        for chunk in split_message(message.render_html()):
            try:
                await self._bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
                )
            except TelegramError as exc:
                logger.error("Telegram mesajı göndərilmədi (chat %s): %s", chat_id, exc)
                return False
        return True
