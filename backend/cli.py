"""Command-line tools: ``python -m backend.cli <əmr>``.

Examples:
    python -m backend.cli init-db
    python -m backend.cli ingest --date 2026-10-05
    python -m backend.cli today
    python -m backend.cli leagues --country Azerbaijan
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from backend.config import PROJECT_ROOT, ConfigError, get_config, get_settings
from backend.container import AppContainer, build_container
from backend.database.migrations import upgrade_to_head
from backend.i18n import t
from backend.i18n.az import LEAGUE_TYPES
from backend.models.constants import RunStatus
from backend.presenters.formatters import (
    format_analysis_report,
    format_daily_analysis,
    format_daily_overview,
    format_ingestion_report,
    format_match_detail,
    format_pyramid,
    format_status,
    text_message,
)
from backend.presenters.messages import MessageBuilder
from backend.scheduler.jobs import next_daily_run
from backend.services.errors import ProviderError
from backend.services.overview import DailyOverview, build_daily_overview
from backend.services.picks import build_daily_analysis, get_match_analysis
from backend.services.system_status import collect_status
from backend.utils.formatting import country_name
from backend.utils.logging import configure_logging, force_utf8_stdio
from backend.utils.timeutils import local_now, local_today

logger = logging.getLogger(__name__)

# argparse has no locale support of its own; its built-in texts are translated here.
_ARGPARSE_TEXTS = {
    "usage: ": "istifadə: ",
    "positional arguments": "əmrlər",
    "options": "parametrlər",
    "show this help message and exit": "bu kömək mesajını göstər və çıx",
    "%(prog)s: error: %(message)s\n": "%(prog)s: xəta: %(message)s\n",
    "the following arguments are required: %s": "bu arqumentlər tələb olunur: %s",
    "invalid choice: %(value)r (choose from %(choices)s)": "yanlış seçim: %(value)r (mümkün seçimlər: %(choices)s)",
    "unrecognized arguments: %s": "tanınmayan arqumentlər: %s",
    "argument %(argument_name)s: %(message)s": "arqument %(argument_name)s: %(message)s",
    "expected one argument": "bir dəyər gözlənilir",
    "invalid %(type)s value: %(value)r": "yanlış dəyər: %(value)r",
}


def _install_argparse_translations() -> None:
    original = argparse._  # type: ignore[attr-defined]
    argparse._ = lambda text: _ARGPARSE_TEXTS.get(text, original(text))  # type: ignore[attr-defined]


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(t("error.invalid_date")) from None


def _print(message: MessageBuilder) -> None:
    print(message.render_plain())


# --------------------------------------------------------------------- commands


def cmd_init_db(_container: AppContainer, _args: argparse.Namespace) -> int:
    print(t("cli.db_ready"))
    return 0


def cmd_ingest(container: AppContainer, args: argparse.Namespace) -> int:
    """Ingest the day's data, then run the analysis on it (the same order as the daily pipeline)."""
    day = args.date or local_today(container.tz)
    report = container.run_ingestion(day)
    _print(format_ingestion_report(report))
    if report.status == RunStatus.FAILED:
        return 1
    print()
    _print(format_analysis_report(container.run_analysis(day)))
    return 0


def _overview(container: AppContainer, args: argparse.Namespace) -> DailyOverview:
    return build_daily_overview(
        container.db, args.date or local_today(container.tz), container.tz, container.daily_cutoff
    )


def cmd_today(container: AppContainer, args: argparse.Namespace) -> int:
    _print(format_daily_overview(_overview(container, args), refresh_hint=t("daily.hint_cli")))
    return 0


def cmd_sync(container: AppContainer, args: argparse.Namespace) -> int:
    """Only the free sources (football-data.co.uk / .org) — no API-Football requests."""
    report = container.run_external_sync(args.date or local_today(container.tz))
    if report is None:
        print(t("cli.sync_disabled"))
        return 1
    print(t("ingest.external", added=report.matches_added, xg=report.with_xg, updated=report.matches_updated,
            standings=report.standings_leagues, scorers=report.scorer_leagues))
    print(t("ingest.external_teams", linked=report.teams_linked, created=report.teams_created))
    for warning in report.warnings:
        print(f"• {warning}")
    return 0


def cmd_analyze(container: AppContainer, args: argparse.Namespace) -> int:
    _print(format_analysis_report(container.run_analysis(args.date or local_today(container.tz))))
    return 0


def _analysis_message(container: AppContainer, day: date, refresh_hint: str | None = None) -> MessageBuilder:
    analysis = build_daily_analysis(container.db, day, container.tz)
    state = container.pyramid_service().get_state()
    return format_daily_analysis(analysis, state, paper_mode=container.settings.paper_mode, refresh_hint=refresh_hint)


def cmd_picks(container: AppContainer, args: argparse.Namespace) -> int:
    _print(_analysis_message(container, args.date or local_today(container.tz), t("daily.hint_cli")))
    return 0


def cmd_match(container: AppContainer, args: argparse.Namespace) -> int:
    view = get_match_analysis(container.db, args.match_id, container.tz)
    _print(format_match_detail(view))
    return 0 if view else 1


def cmd_pyramid(container: AppContainer, _args: argparse.Namespace) -> int:
    service = container.pyramid_service()
    state = service.get_state()
    _print(format_pyramid(state, service.projection(state), paper_mode=container.settings.paper_mode))
    return 0


def cmd_status(container: AppContainer, _args: argparse.Namespace) -> int:
    upcoming = next_daily_run(local_now(container.tz), container.config.schedule.daily_pipeline)
    _print(format_status(collect_status(container, upcoming)))
    return 0


def cmd_leagues(container: AppContainer, args: argparse.Namespace) -> int:
    if not (args.check or args.country or args.search):
        print(t("cli.leagues_need_filter"))
        return 1
    client = container.api_client()
    if args.check:
        for league in container.config.leagues:
            found = client.leagues(league_id=league.api_id)
            if not found:
                print(t("cli.leagues_check_missing", api_id=league.api_id, name_az=league.name_az))
                continue
            info = found[0]
            mismatch = league.country and info.country and league.country.casefold() != info.country.casefold()
            key = "cli.leagues_check_mismatch" if mismatch else "cli.leagues_check_ok"
            print(t(key, api_id=info.api_id, name_az=league.name_az, name=info.name, country=info.country or "—"))
        return 0
    leagues = client.leagues(country=args.country, search=args.search)
    if not leagues:
        print(t("cli.leagues_none"))
        return 0
    print(t("cli.leagues_header"))
    for info in sorted(leagues, key=lambda item: item.api_id):
        print(
            t(
                "cli.leagues_row",
                api_id=info.api_id,
                name=info.name,
                country=country_name(info.country) or "—",
                kind=LEAGUE_TYPES.get(info.league_type or "", info.league_type or "—"),
            )
        )
    return 0


async def _send_telegram(container: AppContainer, message: MessageBuilder) -> bool:
    from telegram import Bot

    from backend.telegram.notifier import TelegramNotifier

    bot = Bot(container.settings.telegram_bot_token.get_secret_value())
    async with bot:
        return await TelegramNotifier(bot, container.settings.telegram_chat_ids).send(message)


def _send(container: AppContainer, message: MessageBuilder) -> int:
    if not container.settings.telegram_configured or not container.settings.telegram_chat_ids:
        print(t("error.telegram_not_configured"))
        return 1
    if asyncio.run(_send_telegram(container, message)):
        print(t("cli.sent"))
        return 0
    print(t("error.telegram_send"))
    return 1


async def _with_bot(container: AppContainer, work: Callable[[Any, Any], Awaitable[int]]) -> int:
    """Run ``work(bot, notifier)``; bot and notifier are None when Telegram is not configured."""
    from telegram import Bot

    from backend.telegram.notifier import TelegramNotifier

    if not container.settings.telegram_configured:
        return await work(None, None)
    async with Bot(container.settings.telegram_bot_token.get_secret_value()) as bot:
        return await work(bot, TelegramNotifier(bot, container.settings.telegram_chat_ids))


def cmd_run_daily(container: AppContainer, args: argparse.Namespace) -> int:
    """Scheduled run (GitHub Actions): the first run of the day sends the analysis, later runs only refresh."""
    from backend.scheduler.pipeline import PipelineRunner
    from backend.services.notifications import was_sent

    day = args.date or local_today(container.tz)

    async def work(_bot: Any, notifier: Any) -> int:
        already_sent = was_sent(container.db, f"daily_analysis:{day.isoformat()}")
        result = await PipelineRunner(container, notifier).run_daily(day, trigger="refresh" if already_sent else "schedule")
        if result is None:
            return 1
        _print(format_ingestion_report(result.ingestion))
        if result.analysis is not None:
            print()
            _print(format_analysis_report(result.analysis))
        return 0

    return asyncio.run(_with_bot(container, work))


def cmd_bot_poll(container: AppContainer, _args: argparse.Namespace) -> int:
    """Answer Telegram messages that queued up since the last run (GitHub Actions)."""
    from backend.scheduler.pipeline import PipelineRunner
    from backend.telegram.poller import poll_once

    async def work(bot: Any, notifier: Any) -> int:
        if bot is None:
            print(t("error.telegram_not_configured"))
            return 1
        handled = await poll_once(container, PipelineRunner(container, notifier), bot, notifier)
        print(t("cli.bot_polled", count=handled))
        return 0

    return asyncio.run(_with_bot(container, work))


def cmd_send_test(container: AppContainer, _args: argparse.Namespace) -> int:
    return _send(container, text_message(t("bot.test_message")))


def cmd_send_today(container: AppContainer, args: argparse.Namespace) -> int:
    return _send(container, _analysis_message(container, args.date or local_today(container.tz)))


def cmd_cache_purge(container: AppContainer, _args: argparse.Namespace) -> int:
    print(t("cli.cache_purged", count=container.cache.purge_expired()))
    return 0


Command = Callable[[AppContainer, argparse.Namespace], int]
COMMANDS: dict[str, Command] = {
    "init-db": cmd_init_db,
    "ingest": cmd_ingest,
    "sync": cmd_sync,
    "analyze": cmd_analyze,
    "today": cmd_today,
    "picks": cmd_picks,
    "match": cmd_match,
    "pyramid": cmd_pyramid,
    "status": cmd_status,
    "leagues": cmd_leagues,
    "run-daily": cmd_run_daily,
    "bot-poll": cmd_bot_poll,
    "send-test": cmd_send_test,
    "send-today": cmd_send_today,
    "cache-purge": cmd_cache_purge,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.cli", description=t("cli.description"))
    sub = parser.add_subparsers(dest="command", required=True, metavar=t("cli.command_metavar"))

    sub.add_parser("init-db", help=t("cli.help.init_db"))
    dated = (
        ("ingest", "cli.help.ingest"),
        ("sync", "cli.help.sync"),
        ("analyze", "cli.help.analyze"),
        ("today", "cli.help.today"),
        ("picks", "cli.help.picks"),
        ("send-today", "cli.help.send_today"),
        ("run-daily", "cli.help.run_daily"),
    )
    for name, help_key in dated:
        command = sub.add_parser(name, help=t(help_key))
        command.add_argument("--date", type=_parse_date, help=t("cli.help.date"))
    match = sub.add_parser("match", help=t("cli.help.match"))
    match.add_argument("match_id", type=int, help=t("cli.help.match_id"))
    sub.add_parser("pyramid", help=t("cli.help.pyramid"))
    sub.add_parser("status", help=t("cli.help.status"))
    leagues = sub.add_parser("leagues", help=t("cli.help.leagues"))
    leagues.add_argument("--country", help=t("cli.help.country"))
    leagues.add_argument("--search", help=t("cli.help.search"))
    leagues.add_argument("--check", action="store_true", help=t("cli.help.check"))
    sub.add_parser("bot-poll", help=t("cli.help.bot_poll"))
    sub.add_parser("send-test", help=t("cli.help.send_test"))
    sub.add_parser("cache-purge", help=t("cli.help.cache_purge"))
    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    _install_argparse_translations()
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
        config = get_config()
    except ConfigError as exc:
        print(exc.user_message)
        return 1

    # Warnings go to the console; full detail goes to logs/app.log.
    configure_logging(settings.log_level, PROJECT_ROOT / "logs", console_level="WARNING")
    container: AppContainer | None = None
    try:
        upgrade_to_head(settings.database_url)
        container = build_container(settings, config)
        return COMMANDS[args.command](container, args)
    except ProviderError as exc:
        logger.debug("CLI provider error: %s", exc.detail)
        print(exc.user_message)
        return 1
    except KeyboardInterrupt:
        print(t("cli.interrupted"))
        return 130
    except Exception:
        logger.exception("CLI əmri uğursuz oldu: %s", args.command)
        print(t("error.unexpected"))
        return 1
    finally:
        if container is not None:
            container.close()


if __name__ == "__main__":
    raise SystemExit(main())
