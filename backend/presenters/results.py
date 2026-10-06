"""Azerbaijani text for settled picks and the live track record."""

from __future__ import annotations

from backend.i18n import t
from backend.models.constants import BetStatus, StageStatus
from backend.presenters.labels import selection_label
from backend.presenters.messages import MessageBuilder, bold
from backend.services.price_checks import BookmakerStats
from backend.services.pyramid import PyramidState
from backend.services.results import AiRecord, SettlementReport, TrackRecord
from backend.utils.formatting import format_money, format_odds, format_percent, format_signed_percent

STATUS_ICONS = {BetStatus.WON: "✅", BetStatus.LOST: "❌", BetStatus.VOID: "↩️"}


def _record_line(record: TrackRecord) -> str:
    if not record.bets:
        return t("results.record_empty", pending=record.pending)
    return t(
        "results.record",
        bets=record.bets,
        wins=record.wins,
        losses=record.losses,
        hit=format_percent(record.hit_rate, 1) if record.hit_rate is not None else "—",
        pnl=f"{record.pnl:+.2f}",
        roi=format_signed_percent(record.roi, 1) if record.roi is not None else "—",
        clv=format_signed_percent(record.avg_clv, 1) if record.avg_clv is not None else "—",
    )


def _pyramid_line(report: SettlementReport) -> str | None:
    state = report.pyramid
    if report.stage_settled is None or state is None:
        return None
    if report.stage_settled == StageStatus.WON:
        return t("results.stage_won", balance=format_money(state.balance), stage=state.stage_no)
    if report.stage_settled == StageStatus.LOST:
        return t("results.stage_lost", attempt=state.attempt_no, start=format_money(state.starting))
    return t("results.stage_void", balance=format_money(state.balance))


def format_settlement(report: SettlementReport, record: TrackRecord) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("results.title")))
    msg.blank()
    for bet in report.settled:
        pick = selection_label(bet.market, bet.selection, bet.line, bet.home, bet.away)
        msg.line(
            t(
                "results.bet",
                icon=STATUS_ICONS.get(BetStatus(bet.status), "•"),
                home=bet.home,
                away=bet.away,
                score=bet.score or "—",
                pick=pick,
                odds=format_odds(bet.odds),
                pnl=f"{bet.pnl:+.2f}",
            )
        )
    day_pnl = sum(b.pnl for b in report.settled)
    msg.blank()
    msg.line(t("results.day_total", pnl=f"{day_pnl:+.2f}"))
    msg.line(_record_line(record))
    pyramid = _pyramid_line(report)
    if pyramid:
        msg.line(pyramid)
    msg.blank()
    msg.line(t("results.paper_note"))
    return msg


def format_track_record(
    overall: TrackRecord,
    top: TrackRecord,
    recent: TrackRecord,
    state: PyramidState,
    backtest: dict | None,
    prices: BookmakerStats | None = None,
    ai: AiRecord | None = None,
) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("results.stats_title")))
    msg.blank()
    msg.line(bold(t("results.all_picks")))
    msg.line(_record_line(overall))
    msg.line(bold(t("results.top_picks")))
    msg.line(_record_line(top))
    msg.line(bold(t("results.last_30")))
    msg.line(_record_line(recent))
    msg.section()
    msg.line(
        t(
            "results.pyramid_state",
            attempt=state.attempt_no,
            stage=state.stage_no,
            balance=format_money(state.balance),
            locked=format_money(state.locked_total),
        )
    )
    if prices is not None and prices.checks:
        msg.line(
            t(
                "results.price_line",
                bookmaker=prices.bookmaker,
                checks=prices.checks,
                gap=format_signed_percent(prices.gap_to_fair, 1) if prices.gap_to_fair is not None else "—",
                passed=prices.passed,
            )
        )
    if ai is not None and ai.reviewed:
        msg.line(
            t(
                "ai.record",
                reviewed=ai.reviewed,
                vetoed=ai.vetoed,
                lost=ai.vetoed_lost,
                won=ai.vetoed_won,
                effect=f"{ai.effect:+.2f}",
            )
        )
    if backtest and backtest.get("summary", {}).get("bets"):
        s = backtest["summary"]
        msg.section()
        msg.line(
            t(
                "results.backtest_line",
                bets=s["bets"],
                hit=format_percent(s["hit_rate"], 1),
                roi=format_signed_percent(s["roi"], 1),
                clv=format_signed_percent(s["avg_clv"], 1) if s.get("avg_clv") is not None else "—",
            )
        )
    msg.blank()
    msg.line(t("results.paper_note"))
    return msg
