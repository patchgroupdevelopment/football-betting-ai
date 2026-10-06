"""Bot command logic, independent of how updates arrive.

Locally the bot uses long polling (python-telegram-bot); on GitHub Actions a
scheduled job fetches pending updates every few minutes. Both call
``respond()``, so replies are identical. ``/yenile`` is left to the caller,
because it runs the whole pipeline and each mode schedules that differently.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.i18n import t
from backend.presenters.formatters import (
    format_daily_overview,
    format_match_detail,
    format_pyramid,
    format_setup_mode,
    format_status,
    format_welcome,
    text_message,
)
from backend.presenters.messages import MessageBuilder
from backend.services.overview import build_daily_overview
from backend.services.picks import get_match_analysis
from backend.services.system_status import collect_status
from backend.services.users import register_user
from backend.utils.timeutils import local_now, local_today

if TYPE_CHECKING:
    from backend.container import AppContainer
    from backend.scheduler.pipeline import PipelineRunner

logger = logging.getLogger(__name__)

MATCH_LINK = re.compile(r"^/oyun_(\d+)")
REFRESH = "yenile"


@dataclass(frozen=True)
class Incoming:
    chat_id: int
    user_name: str | None
    text: str


def parse_command(text: str | None) -> tuple[str, list[str]]:
    """'/oyun_12@bot' -> ('oyun', ['12']); '/bugun' -> ('bugun', []); plain text -> ('', [...])."""
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return "", parts
    name = parts[0].split("@")[0].lstrip("/").lower()
    if name.startswith("oyun_"):
        return "oyun", [name.removeprefix("oyun_"), *parts[1:]]
    return name, parts[1:]


def authorization_reply(container: AppContainer, chat_id: int) -> MessageBuilder | None:
    """None when the chat may use the bot; otherwise the reply to send instead."""
    allowed = container.settings.telegram_chat_ids
    if not allowed:
        return format_setup_mode(chat_id)  # setup mode: tell the sender their chat ID
    if chat_id not in allowed:
        logger.warning("İcazəsiz Telegram istifadəçisi: chat_id=%s", chat_id)
        return text_message(t("bot.unauthorized"))
    return None


async def respond(container: AppContainer, runner: PipelineRunner, incoming: Incoming) -> MessageBuilder:
    denied = authorization_reply(container, incoming.chat_id)
    if denied is not None:
        return denied
    command, args = parse_command(incoming.text)
    today = local_today(container.tz)
    daily_time = container.config.schedule.daily_pipeline

    if command == "start":
        await asyncio.to_thread(register_user, container.db, incoming.chat_id, incoming.user_name)
        return format_welcome(daily_time)
    if command == "komek":
        return format_welcome(daily_time, greeting=False)
    if command == "bugun":
        overview = await asyncio.to_thread(build_daily_overview, container.db, today, container.tz, container.daily_cutoff)
        return format_daily_overview(overview)
    if command == "secimler":
        return await runner.daily_analysis_message(today)
    if command == "oyun":
        digits = next((a for a in args if a.isdigit()), None)
        if digits is None:
            return text_message(t("detail.not_found"))
        view = await asyncio.to_thread(get_match_analysis, container.db, int(digits), container.tz)
        return format_match_detail(view)
    if command == "piramida":
        service = container.pyramid_service()
        state = await asyncio.to_thread(service.get_state)
        return format_pyramid(state, service.projection(state), paper_mode=container.settings.paper_mode)
    if command == "status":
        from backend.scheduler.jobs import next_daily_run  # local: the scheduler imports the bot modules

        # No in-process scheduler on GitHub Actions: the next run follows from the configured time.
        upcoming = runner.next_daily_run() or next_daily_run(local_now(container.tz), daily_time)
        status = await asyncio.to_thread(collect_status, container, upcoming)
        return format_status(status)
    return text_message(t("bot.unknown_command"))
