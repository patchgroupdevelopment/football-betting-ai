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
from datetime import timedelta
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
from backend.backtest.service import latest_report
from backend.presenters.backtest import format_backtest_report
from backend.presenters.messages import MessageBuilder
from backend.presenters.formatters import min_odds
from backend.presenters.price_check import MAX_ODDS, MIN_ODDS, format_bookmaker_stats, format_price_check, price_value
from backend.presenters.results import format_track_record
from backend.services.overview import build_daily_overview
from backend.services.picks import get_match_analysis
from backend.services.price_checks import bookmaker_stats, record_check
from backend.services.results import track_record
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
    if name.startswith("misli_"):
        return "misli", [name.removeprefix("misli_"), *parts[1:]]
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


def price_check_message(container: AppContainer, args: list[str], chat_id: int | None = None) -> MessageBuilder:
    """/misli_12 1.45 — is the price at the user's bookmaker worth taking? /misli — what the checks show."""
    bookmaker = container.config.selection.user_bookmaker or "Misli.az"
    match_id = next((int(a) for a in args if a.isdigit()), None)
    odds = next((_parse_odds(a) for a in args if not a.isdigit() and _parse_odds(a) is not None), None)
    if match_id is None:
        return format_bookmaker_stats(bookmaker_stats(container.db, bookmaker))
    view = get_match_analysis(container.db, match_id, container.tz)
    state = container.pyramid_service().get_state()
    message = format_price_check(view, odds, bookmaker, state)
    valid = view is not None and view.has_candidate and view.p_final and odds is not None
    if valid and MIN_ODDS <= odds <= MAX_ODDS:
        record_check(container.db, view, odds, bookmaker, price_value(view, odds), min_odds(view), chat_id)
    return message


def _parse_odds(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def track_record_message(container: AppContainer) -> MessageBuilder:
    today = local_today(container.tz)
    return format_track_record(
        track_record(container.db),
        track_record(container.db, top_only=True),
        track_record(container.db, since=today - timedelta(days=30)),
        container.pyramid_service().get_state(),
        latest_report(container.db),
        bookmaker_stats(container.db, container.config.selection.user_bookmaker)
        if container.config.selection.user_bookmaker
        else None,
    )


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
        return format_match_detail(view, user_bookmaker=container.config.selection.user_bookmaker)
    if command == "piramida":
        service = container.pyramid_service()
        state = await asyncio.to_thread(service.get_state)
        return format_pyramid(state, service.projection(state), paper_mode=container.settings.paper_mode)
    if command == "misli":
        return await asyncio.to_thread(price_check_message, container, args, incoming.chat_id)
    if command == "neticeler":
        return await asyncio.to_thread(track_record_message, container)
    if command == "backtest":
        report = await asyncio.to_thread(latest_report, container.db)
        return format_backtest_report(report, detailed=False)
    if command == "status":
        from backend.scheduler.jobs import next_daily_run  # local: the scheduler imports the bot modules

        # No in-process scheduler on GitHub Actions: the next run follows from the configured time.
        upcoming = runner.next_daily_run() or next_daily_run(local_now(container.tz), daily_time)
        status = await asyncio.to_thread(collect_status, container, upcoming)
        return format_status(status)
    return text_message(t("bot.unknown_command"))
