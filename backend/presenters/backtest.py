"""Azerbaijani text of a backtest report (CLI and Telegram)."""

from __future__ import annotations

from datetime import date

from backend.i18n import t
from backend.i18n.az import MARKETS, PYRAMID_MODES
from backend.presenters.labels import selection_label
from backend.presenters.messages import MessageBuilder, bold
from backend.utils.formatting import format_date, format_money, format_odds, format_percent, format_signed_percent

PYRAMID_STRATEGIES = {"daily": "backtest.pyramid.daily", "chain": "backtest.pyramid.chain"}
SHOWN_BREAKS = 6


def _pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    return format_signed_percent(value, 1) if signed else format_percent(value, 1)


def _day(iso: str) -> str:
    return format_date(date.fromisoformat(iso))


def _summary_lines(msg: MessageBuilder, s: dict) -> None:
    if not s.get("bets"):
        msg.line(t("backtest.no_bets"))
        return
    msg.line(t("backtest.bets", bets=s["bets"], wins=s["wins"], losses=s["losses"], pushes=s["pushes"]))
    msg.line(t("backtest.hit_rate", actual=_pct(s["hit_rate"]), expected=_pct(s["expected_hit_rate"])))
    msg.line(t("backtest.avg_odds", odds=format_odds(s["avg_odds"])))
    msg.line(t("backtest.profit", profit=f"{s['profit']:+.2f}", roi=_pct(s["roi"], signed=True)))
    msg.line(t("backtest.drawdown", drawdown=f"{s['max_drawdown']:.2f}", streak=s["longest_losing_streak"]))
    if s.get("clv_bets"):
        msg.line(t("backtest.clv", clv=_pct(s["avg_clv"], signed=True), share=_pct(s["clv_positive"])))


def _stage_text(stage: dict) -> str:
    pick = selection_label(stage["market"], stage["selection"], stage["line"], stage["home"], stage["away"])
    return t(
        "backtest.pyramid.stage",
        day=_day(stage["day"]),
        home=stage["home"],
        away=stage["away"],
        score=stage["score"],
        pick=pick,
        odds=format_odds(stage["odds"]),
    )


def _pyramid(msg: MessageBuilder, key: str, p: dict, *, detailed: bool) -> None:
    strategy, mode = key.split("_", 1)
    msg.line(bold(t(PYRAMID_STRATEGIES[strategy], mode=PYRAMID_MODES.get(mode, mode))))
    if not p.get("attempts"):
        msg.line(t("backtest.no_bets"))
        return
    best = p["max_attempt"]
    msg.line(
        t(
            "backtest.pyramid.max",
            amount=format_money(p["max_balance"]),
            wins=best["wins"],
            start=_day(best["start"]),
            end=_day(best["peak_day"]),
        )
    )
    msg.line(t("backtest.pyramid.attempts", attempts=p["attempts"], invested=format_money(p["invested"]), targets=p["targets_reached"]))
    if p.get("locked_total"):
        net = p["locked_total"] - p["invested"]
        msg.line(t("backtest.pyramid.locked", locked=format_money(p["locked_total"]), net=f"{net:+.2f}"))
    else:
        msg.line(t("backtest.pyramid.nothing_kept", invested=format_money(p["invested"])))
    if p.get("avg_wins_before_break") is not None:
        msg.line(t("backtest.pyramid.avg_break", wins=p["avg_wins_before_break"], longest=p["longest_run"]))
    if not detailed:
        return
    histogram = p.get("break_histogram") or {}
    if histogram:
        parts = ", ".join(t("backtest.pyramid.break_item", stage=k, count=v) for k, v in histogram.items())
        msg.line(t("backtest.pyramid.breaks", parts=parts))
    broke = next((a["broke_at"] for a in p["attempt_list"] if a["number"] == best["number"] and a["broke_at"]), None)
    if broke:
        msg.line(t("backtest.pyramid.broke_on", amount=format_money(broke["balance_before"]), stage=_stage_text(broke)))


def format_backtest_report(report: dict | None, *, detailed: bool = True) -> MessageBuilder:
    msg = MessageBuilder()
    msg.line(bold(t("backtest.title")))
    if not report:
        msg.blank()
        msg.line(t("backtest.missing"))
        return msg

    period = report["period"]
    params = report["params"]
    msg.line(t("backtest.period", start=_day(period["start"]), end=_day(period["end"]), matches=report["matches"], leagues=len(report["leagues"])))
    msg.line(
        t(
            "backtest.rules",
            weight=_pct(params["model_weight"]),
            ev=_pct(params["min_ev"]),
            confidence=params["min_confidence"],
            picks=params["max_picks"],
        )
    )

    msg.section()
    msg.line(bold(t("backtest.summary_title")))
    _summary_lines(msg, report["summary"])
    median = report.get("median_price") or {}
    msg.line(t("backtest.median_price", bets=median.get("bets", 0), roi=_pct(median.get("roi"), signed=True)))
    baseline = report.get("baseline") or {}
    if baseline.get("bets"):
        msg.line(t("backtest.baseline", bets=baseline["bets"], roi=_pct(baseline["roi"], signed=True)))

    periods = report.get("periods") or {}
    if periods:
        msg.blank()
        for months, s in periods.items():
            msg.line(t("backtest.period_line", months=months, bets=s["bets"], roi=_pct(s["roi"], signed=True), hit=_pct(s["hit_rate"])))

    if detailed and report.get("by_market"):
        msg.blank()
        msg.line(bold(t("backtest.by_market")))
        for market, s in report["by_market"].items():
            msg.line(t("backtest.group_line", name=MARKETS.get(market, market), bets=s["bets"], roi=_pct(s["roi"], signed=True), hit=_pct(s["hit_rate"])))

    scores = report.get("probability_scores") or {}
    if scores.get("model") and scores.get("market"):
        msg.blank()
        msg.line(bold(t("backtest.calibration_title")))
        msg.line(t("backtest.brier", model=scores["model"]["brier"], market=scores["market"]["brier"], closing=scores.get("closing", {}).get("brier", "—")))

    msg.section()
    msg.line(bold(t("backtest.pyramid_title")))
    for key, p in (report.get("pyramid") or {}).items():
        if not detailed and not key.startswith("daily"):
            continue
        msg.blank()
        _pyramid(msg, key, p, detailed=detailed)

    sweep = report.get("sweep")
    if sweep:
        msg.section()
        msg.line(bold(t("backtest.sweep_title")))
        msg.line(t("backtest.sweep_count", total=sweep["combinations"], robust=sweep["robust"]))
        chosen = sweep.get("chosen")
        if chosen:
            msg.line(
                t(
                    "backtest.sweep_chosen",
                    weight=_pct(chosen["model_weight"]),
                    ev=_pct(chosen["min_ev"]),
                    confidence=chosen["min_confidence"],
                    first=_pct(chosen["first_roi"], signed=True),
                    second=_pct(chosen["second_roi"], signed=True),
                    clv=_pct(chosen["avg_clv"], signed=True),
                )
            )
            msg.line(t("backtest.sweep_applied") if sweep.get("applied") else t("backtest.sweep_not_applied"))
        else:
            msg.line(t("backtest.sweep_none"))

    msg.section()
    for note in report.get("notes", []):
        msg.line(t(f"backtest.note.{note}"))
    msg.line(t("backtest.disclaimer"))
    return msg
