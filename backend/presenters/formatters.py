"""Azerbaijani report formatting shared by Telegram, the CLI and (later) the dashboard."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date

from backend.i18n import t
from backend.i18n.az import (
    AI_VERDICT_ICONS,
    AI_VERDICTS,
    DATA_QUALITY_ICONS,
    DATA_QUALITY_LEVELS,
    DECISION_ICONS,
    DECISIONS,
    FACTOR_NAMES,
    MATCH_STATUSES,
    PYRAMID_MODES,
    RUN_STATUSES,
)
from backend.models.constants import FINISHED_STATUSES, LIVE_STATUSES, NOT_STARTED_STATUSES, Decision
from backend.presenters.labels import reasons_text, risk_label, selection_label
from backend.presenters.messages import MessageBuilder, bold, code
from backend.services.analysis import AnalysisReport
from backend.services.ingestion import IngestionReport
from backend.services.overview import DailyOverview, MatchLine
from backend.services.picks import DailyAnalysis, PickView
from backend.services.pyramid import PyramidProjection, PyramidState, PyramidStep, next_balance
from backend.services.system_status import SystemStatus
from backend.utils.formatting import (
    confidence_band,
    format_date,
    format_date_with_weekday,
    format_datetime,
    format_money,
    format_number,
    format_odds,
    format_signed_percent,
    format_small_probability,
    format_time,
)

# Telegram command names must be Latin (a-z, 0-9, _); descriptions are Azerbaijani.
BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("bugun", "bot.cmd.bugun"),
    ("secimler", "bot.cmd.secimler"),
    ("piramida", "bot.cmd.piramida"),
    ("neticeler", "bot.cmd.neticeler"),
    ("backtest", "bot.cmd.backtest"),
    ("misli", "bot.cmd.misli"),
    ("status", "bot.cmd.status"),
    ("yenile", "bot.cmd.yenile"),
    ("komek", "bot.cmd.komek"),
)
MAX_WARNINGS = 8
PATH_HEAD = 5
PATH_TAIL = 2


def text_message(text: str) -> MessageBuilder:
    return MessageBuilder().line(text)


def _append_warnings(msg: MessageBuilder, warnings: Sequence[str]) -> None:
    if not warnings:
        return
    msg.blank()
    msg.line(bold(t("common.warnings_title")))
    for warning in warnings[:MAX_WARNINGS]:
        msg.line("• ", warning)
    if len(warnings) > MAX_WARNINGS:
        msg.line(t("common.more_warnings", count=len(warnings) - MAX_WARNINGS))


def _append_disclaimer(msg: MessageBuilder) -> MessageBuilder:
    msg.section()
    msg.line(bold(t("disclaimer.title")))
    msg.blank()
    msg.line(t("disclaimer.body"))
    return msg


# ---------------------------------------------------------------- daily report


def _match_headline(match: MatchLine) -> str:
    teams = f"{match.home} – {match.away}"
    if match.status in NOT_STARTED_STATUSES:
        return f"🕐 {format_time(match.kickoff_local)}  {teams}"
    home_goals = match.home_goals if match.home_goals is not None else 0
    away_goals = match.away_goals if match.away_goals is not None else 0
    score = f"{home_goals}:{away_goals}"
    status = MATCH_STATUSES.get(match.status, match.status)
    if match.status in FINISHED_STATUSES:
        return f"✅ {match.home} {score} {match.away}"
    if match.status in LIVE_STATUSES:
        return f"🔴 {match.home} {score} {match.away} ({status})"
    return f"⛔ {format_time(match.kickoff_local)}  {teams} ({status})"


def _quality_line(match: MatchLine) -> str:
    level = match.quality.level
    return t(
        "daily.data_line",
        score=match.quality.score,
        icon=DATA_QUALITY_ICONS[level],
        level=DATA_QUALITY_LEVELS[level],
        odds=t("common.yes") if match.has_odds else t("common.no"),
    )


def format_daily_overview(overview: DailyOverview, *, refresh_hint: str | None = None) -> MessageBuilder:
    """``refresh_hint`` tells how to load missing data; defaults to the Telegram command."""
    msg = MessageBuilder()
    msg.line(bold(t("daily.title")))
    msg.blank()
    msg.line("📅 ", format_date_with_weekday(overview.target_date))
    msg.section()

    if not overview.loaded:
        msg.line(t("daily.not_loaded"))
        msg.line(refresh_hint or t("daily.hint_telegram"))
        _append_warnings(msg, overview.warnings)
        return _append_disclaimer(msg)

    if overview.blocks:
        msg.line(t("daily.found", count=overview.priority_count))
        msg.line(t("daily.readiness", ready=overview.ready_count, incomplete=overview.incomplete_count))
    else:
        msg.line(t("daily.none"))
    if overview.other_leagues_count:
        msg.line(t("daily.other_leagues", count=overview.other_leagues_count))

    for block in overview.blocks:
        msg.blank()
        msg.line("🏆 ", bold(block.title))
        for match in block.matches:
            msg.line(_match_headline(match))
            msg.line("      ", _quality_line(match))
            if match.decision:
                msg.line(
                    "      ",
                    t(
                        "daily.decision_line",
                        icon=DECISION_ICONS.get(match.decision, ""),
                        decision=DECISIONS.get(match.decision, match.decision),
                        match_id=match.match_id,
                    ),
                )

    _append_warnings(msg, overview.warnings)
    if not any(m.decision for block in overview.blocks for m in block.matches):
        msg.section()
        msg.line(t("daily.model_inactive"))
    return _append_disclaimer(msg)


# ------------------------------------------------------------ ingestion report


def format_ingestion_report(report: IngestionReport) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("ingest.title")))
    msg.blank()
    msg.line(t("ingest.date", date=format_date(report.target_date)))
    msg.line(t("ingest.status", status=RUN_STATUSES.get(report.status, report.status)))
    msg.line(t("ingest.fixtures", total=format_number(report.total_fixtures), priority=report.priority_fixtures))
    msg.line(t("ingest.deep", done=report.deep_completed, planned=report.deep_planned))
    msg.line(t("ingest.requests", used=report.requests_used))
    if report.daily_remaining is not None:
        msg.line(t("ingest.remaining", remaining=format_number(report.daily_remaining)))
    if report.duration_seconds is not None:
        msg.line(t("ingest.duration", seconds=round(report.duration_seconds)))
    external = report.external
    if external is not None:
        msg.blank()
        msg.line(
            t(
                "ingest.external",
                added=format_number(external.matches_added),
                xg=format_number(external.with_xg),
                updated=format_number(external.matches_updated),
                standings=external.standings_leagues,
                scorers=external.scorer_leagues,
            )
        )
        msg.line(t("ingest.external_teams", linked=external.teams_linked, created=external.teams_created))
    if report.notes:
        msg.blank()
        for note in report.notes:
            msg.line(note)
    _append_warnings(msg, [*report.warnings, *(external.warnings if external is not None else [])])
    return msg


# --------------------------------------------------------------------- pyramid


def _abbreviate(steps: Sequence[PyramidStep]) -> Iterable[PyramidStep | None]:
    if len(steps) <= PATH_HEAD + PATH_TAIL + 1:
        return steps
    return [*steps[:PATH_HEAD], None, *steps[-PATH_TAIL:]]


def format_pyramid(state: PyramidState, projection: PyramidProjection, *, paper_mode: bool) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("pyramid.title")))
    if paper_mode:
        msg.line(t("pyramid.paper"))
    msg.blank()
    msg.line(t("pyramid.mode", mode=PYRAMID_MODES.get(state.mode, state.mode)))
    msg.line(t("pyramid.attempt", attempt=state.attempt_no))
    msg.line(t("pyramid.start", amount=format_money(state.starting)))
    msg.line(t("pyramid.balance", amount=format_money(state.balance)))
    msg.line(t("pyramid.stage", stage=state.stage_no))
    msg.line(t("pyramid.target", amount=format_money(state.target)))
    msg.line(t("pyramid.remaining", amount=format_money(state.remaining)))
    average = format_odds(state.average_odds) if state.average_odds else t("pyramid.no_bets")
    msg.line(t("pyramid.avg_odds", odds=average))
    if projection.stages_needed is not None and not state.target_reached:
        msg.line(t("pyramid.stages_left", count=projection.stages_needed, odds=format_odds(projection.odds)))
    if state.mode == "milestone_lock" or state.locked_total:
        msg.line(t("pyramid.locked", amount=format_money(state.locked_total)))
    msg.line(t("pyramid.total_staked", amount=format_money(state.total_staked), attempts=state.attempt_no))

    if state.pending_odds is not None:
        msg.blank()
        msg.line(
            t(
                "pyramid.pending",
                odds=format_odds(state.pending_odds),
                amount=format_money(next_balance(state.balance, state.pending_odds)),
            )
        )

    if state.target_reached:
        msg.blank()
        msg.line(bold(t("pyramid.target_reached")))
        return msg

    if projection.steps:
        msg.section()
        msg.line(bold(t("pyramid.path_title", odds=format_odds(projection.odds))))
        for step in _abbreviate(projection.steps):
            if step is None:
                msg.line(t("pyramid.path_gap"))
            else:
                msg.line(
                    t(
                        "pyramid.path_step",
                        stage=step.stage_no,
                        before=format_money(step.balance_before),
                        after=format_money(step.balance_after),
                    )
                )
        msg.blank()
        msg.line(
            t(
                "pyramid.probability",
                ev=f"{projection.assumed_ev * 100:.0f}",
                probability=format_small_probability(projection.success_probability),
            )
        )
        msg.line(t("pyramid.probability_note"))
    return msg


# ---------------------------------------------------------------------- status


def format_status(status: SystemStatus) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("status.title")))
    msg.blank()
    msg.line(t("status.mode_paper") if status.paper_mode else t("status.mode_live"))

    if status.last_run_date is not None:
        when = (
            format_datetime(status.last_run_finished_local)
            if status.last_run_finished_local
            else format_date(status.last_run_date)
        )
        value = t("status.last_run_value", date=when, status=RUN_STATUSES.get(status.last_run_status or "", ""))
    else:
        value = t("status.never")
    msg.line(t("status.last_run", value=value))

    quota = status.quota
    if quota is not None and quota.limit is not None:
        msg.line(t("status.api_plan", plan=quota.plan or t("common.unknown")))
        msg.line(
            t(
                "status.api_quota",
                used=format_number(quota.used or 0),
                remaining=format_number(quota.remaining or 0),
                limit=format_number(quota.limit),
            )
        )
    else:
        reason = (status.quota_error or t("common.unknown")).removeprefix("⚠️ ")
        msg.line(t("status.api_unavailable", reason=reason))

    msg.line(
        t(
            "status.db",
            matches=format_number(status.matches),
            teams=format_number(status.teams),
            odds=format_number(status.odds),
        )
    )
    msg.line(t("status.telegram", value=t("status.enabled") if status.telegram_enabled else t("status.disabled")))
    reviewers = ", ".join(status.llm_reviewers)
    msg.line(t("status.llm", value=t("status.llm_active", reviewers=reviewers) if reviewers else t("status.disabled")))
    next_run = format_datetime(status.next_run_local) if status.next_run_local else t("status.not_scheduled")
    msg.line(t("status.next_run", value=next_run))
    return msg


# ------------------------------------------------------------------------- bot


def format_welcome(daily_time: str, *, greeting: bool = True) -> MessageBuilder:
    msg = MessageBuilder()
    if greeting:
        msg.line(t("bot.welcome"))
        msg.blank()
    msg.line(bold(t("bot.commands_title")))
    for command, key in BOT_COMMANDS:
        msg.line(f"/{command} — {t(key)}")
    msg.blank()
    msg.line(t("bot.help_footer", time=daily_time))
    return msg


def format_setup_mode(chat_id: int) -> MessageBuilder:
    return (
        MessageBuilder()
        .line(t("bot.setup_mode_title"))
        .line(t("bot.your_chat_id"), code(chat_id))
        .line(t("bot.setup_mode_hint"))
    )


# -------------------------------------------------------------------- analysis


def _p(probability: float | None, digits: int = 1) -> str:
    return "—" if probability is None else f"{probability * 100:.{digits}f}"


def _when(view: PickView, target_date: date | None) -> str:
    time = format_time(view.kickoff_local)
    if target_date is not None and view.kickoff_local.date() != target_date:
        return t("analysis.league_datetime", league=view.league, date=format_date(view.kickoff_local.date()), time=time)
    return t("analysis.league_time", league=view.league, time=time)


def _label(view: PickView) -> str:
    return selection_label(view.market, view.selection, view.line, view.home, view.away)


def _verdict(view: PickView) -> str:
    reasons = view.reasons
    if view.decision == Decision.BET:
        factors = [FACTOR_NAMES[f] for f in reasons.get("top_factors", []) if f in FACTOR_NAMES]
        return t("verdict.bet", factors=", ".join(factors)) if factors else t("verdict.bet_plain")
    keys = reasons.get("not_bet") or reasons.get("match_flags") or ["no_market_in_range"]
    return t("verdict.watch" if view.decision == Decision.WATCH else "verdict.no_bet", reasons=reasons_text(keys))


def min_odds(view: PickView) -> float | None:
    """The lowest price at which the pick still has non-negative value (fair odds, rounded up)."""
    if not view.p_final:
        return None
    return math.ceil(100 / view.p_final - 1e-9) / 100


def _example_odds(view: PickView) -> str:
    return format_odds(view.odds or min_odds(view) or 1.5)


def _pick_numbers(msg: MessageBuilder, view: PickView, user_bookmaker: str | None = None) -> None:
    msg.line(t("pick.bet", label=_label(view)))
    if view.odds:
        msg.line(t("pick.odds", odds=format_odds(view.odds), bookmaker=view.bookmaker or "—"))
        msg.line(t("pick.model", final=_p(view.p_final), model=_p(view.p_model), market=_p(view.p_market)))
        msg.line(t("pick.implied", implied=_p(1 / view.odds, 2)))
    if view.edge_pp is not None:
        msg.line(t("pick.edge", edge=f"{view.edge_pp:+.2f}"))
    if view.ev is not None:
        msg.line(t("pick.ev", ev=format_signed_percent(view.ev)))
    if view.confidence is not None:
        msg.line(t("pick.confidence", confidence=view.confidence, band=confidence_band(view.confidence)))
    icon, risk = risk_label(view.risk)
    if risk:
        msg.line(t("pick.risk", icon=icon, risk=risk))
    if view.fair_odds:
        msg.line(t("pick.fair_odds", fair=format_odds(view.fair_odds)))
    minimum = min_odds(view)
    if user_bookmaker and minimum:
        msg.line(
            t(
                "pick.user_bookmaker",
                bookmaker=user_bookmaker,
                fair=format_odds(minimum),
                match_id=view.match_id,
                example=_example_odds(view),
            )
        )


def _ai_section(msg: MessageBuilder, view: PickView) -> None:
    ai = view.ai
    if not ai or not (ai.get("reviews") or ai.get("failed")):
        return
    msg.section()
    msg.line(bold(t("ai.title")))
    reviews = ai.get("reviews") or []
    for review in reviews:
        msg.line(
            t(
                "ai.review",
                icon=AI_VERDICT_ICONS.get(review["verdict"], ""),
                label=review["label"],
                verdict=AI_VERDICTS.get(review["verdict"], review["verdict"]),
                summary=review.get("summary") or "—",
            )
        )
    risks = [r for review in reviews for r in review.get("risks") or []][:4]
    _bullets(msg, "ai.risks", risks)
    news = [n for review in reviews for n in review.get("news") or []][:4]
    _bullets(msg, "ai.news", news)
    titles = [s.get("title") or s.get("url") for review in reviews for s in review.get("sources") or []]
    if titles:
        msg.line(t("ai.sources", sources=", ".join(dict.fromkeys(titles[:4]))))
    if ai.get("veto"):
        msg.blank()
        msg.line(bold(t("ai.veto")))
    elif ai.get("applied_pp") and ai.get("p_before") is not None and view.p_final is not None:
        msg.line(
            t(
                "ai.adjusted",
                pp=f"{ai['applied_pp']:+.1f}",
                before=f"{ai['p_before'] * 100:.1f}",
                after=f"{view.p_final * 100:.1f}",
            )
        )
    if ai.get("failed"):
        msg.line(t("ai.failed", labels=", ".join(ai["failed"])))
    msg.line(t("ai.note"))


def _ai_short(view: PickView) -> str | None:
    reviews = (view.ai or {}).get("reviews") or []
    if not reviews:
        return None
    items = " · ".join(f"{r['label']} {AI_VERDICT_ICONS.get(r['verdict'], '')}" for r in reviews)
    return t("ai.short", items=items)


def _bullets(msg: MessageBuilder, title_key: str, lines: Sequence[str]) -> None:
    if not lines:
        return
    msg.blank()
    msg.line(bold(t(title_key)))
    for line in lines:
        msg.line("• ", line)


def _reasoning(msg: MessageBuilder, view: PickView) -> None:
    analysis = view.reasons.get("analysis") or []
    if analysis:
        msg.section()
        msg.line(bold(t("analysis.section_analysis")))
        msg.blank()
        for line in analysis:
            msg.line("• ", line)
    msg.section()
    msg.line(bold(t("analysis.verdict_title")))
    msg.line(_verdict(view))
    _bullets(msg, "analysis.why_title", view.reasons.get("why") or [])
    _bullets(msg, "analysis.against_title", view.reasons.get("against") or [])


def _pyramid_section(msg: MessageBuilder, state: PyramidState, best: PickView | None) -> None:
    msg.section()
    msg.line(bold(t("analysis.pyramid_title")))
    msg.blank()
    msg.line(t("analysis.pyramid_stage", stage=state.stage_no, attempt=state.attempt_no))
    msg.line(t("analysis.pyramid_balance", amount=format_money(state.balance)))
    if best is not None and best.odds:
        msg.line(t("analysis.pyramid_odds", odds=format_odds(best.odds)))
        msg.line(t("analysis.pyramid_next", amount=format_money(next_balance(state.balance, best.odds))))
    else:
        msg.line(t("analysis.pyramid_idle"))
    msg.line(t("analysis.pyramid_target", amount=format_money(state.target)))


def _counts_line(analysis: DailyAnalysis) -> str:
    counts = analysis.counts
    return t(
        "analysis.counts",
        total=analysis.total,
        bet=counts.get("bet", 0),
        watch=counts.get("watch", 0),
        no_bet=counts.get("no_bet", 0),
    )


def _other_picks(msg: MessageBuilder, picks: Sequence[PickView], user_bookmaker: str | None = None) -> None:
    msg.section()
    msg.line(bold(t("analysis.other_picks")))
    msg.blank()
    for view in picks:
        icon, risk = risk_label(view.risk)
        msg.line(
            t(
                "analysis.other_line",
                rank=view.rank,
                home=view.home,
                away=view.away,
                label=_label(view),
                odds=format_odds(view.odds or 0),
                confidence=view.confidence or 0,
                risk_icon=icon,
                risk=risk,
            )
        )
        minimum = min_odds(view)
        if user_bookmaker and minimum:
            msg.line(
                t(
                    "pick.user_bookmaker_short",
                    bookmaker=user_bookmaker,
                    fair=format_odds(minimum),
                    match_id=view.match_id,
                    example=_example_odds(view),
                )
            )
        short = _ai_short(view)
        if short:
            msg.line(short)
        msg.line(t("analysis.detail_link", match_id=view.match_id))


def _no_bet_section(msg: MessageBuilder, analysis: DailyAnalysis) -> None:
    msg.line(bold(t("analysis.no_bet_title")))
    msg.blank()
    msg.line(t("analysis.no_bet_body"))
    if analysis.no_bet_reasons:
        msg.blank()
        msg.line(t("analysis.no_bet_reasons"))
        for key, count in list(analysis.no_bet_reasons.items())[:4]:
            msg.line(t("analysis.reason_count", reason=reasons_text([key]), count=count))
    if not analysis.watch:
        return
    msg.section()
    msg.line(bold(t("analysis.watch_title")))
    msg.blank()
    for n, view in enumerate(analysis.watch, start=1):
        msg.line(
            t(
                "analysis.watch_line",
                n=n,
                home=view.home,
                away=view.away,
                label=_label(view),
                odds=format_odds(view.odds or 0),
                confidence=view.confidence or 0,
                ev=format_signed_percent(view.ev or 0),
            )
        )
        msg.line(
            t("analysis.watch_reason", reasons=reasons_text(view.reasons.get("not_bet") or []), match_id=view.match_id)
        )


def format_daily_analysis(
    analysis: DailyAnalysis,
    state: PyramidState,
    *,
    paper_mode: bool,
    refresh_hint: str | None = None,
    user_bookmaker: str | None = None,
) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("analysis.title")))
    msg.blank()
    msg.line("📅 ", format_date_with_weekday(analysis.target_date))
    msg.section()

    if not analysis.analyzed:
        msg.line(t("analysis.not_run"))
        msg.line(refresh_hint or t("daily.hint_telegram"))
        return _append_disclaimer(msg)
    if analysis.total == 0:
        msg.line(t("analysis.no_matches"))
        _pyramid_section(msg, state, None)
        return _append_disclaimer(msg)

    if analysis.picks:
        best = analysis.picks[0]
        msg.line(bold(t("analysis.best_pick")))
        msg.blank()
        msg.line(t("analysis.match_line", home=best.home, away=best.away))
        msg.line(_when(best, analysis.target_date))
        msg.blank()
        _pick_numbers(msg, best, user_bookmaker)
        _reasoning(msg, best)
        _ai_section(msg, best)
        if len(analysis.picks) > 1:
            _other_picks(msg, analysis.picks[1:], user_bookmaker)
    else:
        _no_bet_section(msg, analysis)

    msg.section()
    msg.line(_counts_line(analysis))
    _pyramid_section(msg, state, analysis.picks[0] if analysis.picks else None)
    if paper_mode:
        msg.blank()
        msg.line(t("analysis.paper_note"))
    return _append_disclaimer(msg)


def format_match_detail(view: PickView | None, *, user_bookmaker: str | None = None) -> MessageBuilder:
    if view is None:
        return text_message(t("detail.not_found"))
    msg = MessageBuilder()
    msg.line(bold(t("detail.title", home=view.home, away=view.away)))
    msg.line(
        t(
            "analysis.league_datetime",
            league=view.league,
            date=format_date(view.kickoff_local.date()),
            time=format_time(view.kickoff_local),
        )
    )
    msg.blank()
    msg.line(
        bold(
            t(
                "detail.decision",
                icon=DECISION_ICONS.get(view.decision, ""),
                decision=DECISIONS.get(view.decision, view.decision),
            )
        )
    )
    if view.has_candidate:
        msg.blank()
        _pick_numbers(msg, view, user_bookmaker)
    quality = view.reasons.get("quality") or {}
    if quality:
        level = quality.get("level", "low")
        msg.line(
            t(
                "pick.quality",
                score=quality.get("score", 0),
                icon=DATA_QUALITY_ICONS.get(level, ""),
                level=DATA_QUALITY_LEVELS.get(level, level),
            )
        )

    candidates = view.reasons.get("candidates") or []
    if candidates:
        msg.section()
        msg.line(bold(t("detail.candidates_title")))
        msg.blank()
        for item in candidates:
            msg.line(
                t(
                    "detail.candidate_line",
                    icon=DECISION_ICONS.get(item["decision"], ""),
                    label=selection_label(item["market"], item["selection"], item["line"], view.home, view.away),
                    odds=format_odds(item["odds"]),
                    model=_p(item["p_model"]),
                    market=_p(item["p_market"]),
                    ev=format_signed_percent(item["ev"]),
                    confidence=item["confidence"],
                )
            )
    _reasoning(msg, view)
    _ai_section(msg, view)
    return _append_disclaimer(msg)


def format_analysis_report(report: AnalysisReport) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("analysis_run.title")))
    msg.blank()
    msg.line(t("ingest.date", date=format_date(report.target_date)))
    msg.line(
        t(
            "analysis_run.summary",
            analyzed=report.analyzed,
            bets=report.bets,
            watch=report.watch,
            no_bet=report.no_bet,
        )
    )
    msg.line(t("analysis_run.model", matches=format_number(report.matches_used)))
    if report.ai_reviewed:
        msg.line(t("analysis_run.ai", reviewed=report.ai_reviewed, vetoed=report.ai_vetoed))
    _append_warnings(msg, report.warnings)
    return msg
