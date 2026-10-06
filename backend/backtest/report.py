"""The backtest report: a JSON-serialisable dict stored in the database and shown by the dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from backend.backtest.engine import BacktestRun
from backend.backtest.metrics import (
    breakdown,
    calibration,
    confidence_bucket,
    odds_bucket,
    probability_scores,
    pyramid_summary,
    summarize,
)
from backend.backtest.simulate import Params, SimBet, select_bets, simulate_pyramid
from backend.backtest.sweep import SweepRow, row_view
from backend.config import AppConfig
from backend.utils.timeutils import utcnow

PERIOD_DAYS = {"3": 91, "6": 182, "12": 365}
PYRAMID_VARIANTS = (("daily", "classic"), ("daily", "milestone_lock"), ("chain", "classic"), ("chain", "milestone_lock"))
SWEEP_ROWS_SHOWN = 12

# Limitations, rendered in Azerbaijani by the presenters (i18n "backtest.note.<key>").
NOTES = ("best_price", "markets", "absences", "in_sample")


def params_from_config(config: AppConfig) -> Params:
    selection = config.selection
    return Params(
        model_weight=config.model.market_blend_weight,
        min_ev=selection.min_ev,
        min_confidence=selection.min_confidence,
        max_picks=selection.max_daily_picks,
        min_odds=selection.min_odds,
        max_odds=selection.max_odds,
    )


def params_view(params: Params) -> dict:
    return {
        "model_weight": params.model_weight,
        "min_ev": params.min_ev,
        "min_confidence": params.min_confidence,
        "max_picks": params.max_picks,
    }


def _bet_view(bet: SimBet, run: BacktestRun, titles: dict[int, str]) -> dict:
    record = bet.record
    match = run.match(record.match_index)
    return {
        "day": record.day.isoformat(),
        "rank": bet.rank,
        "league": titles.get(record.league_id, ""),
        "home": match.home,
        "away": match.away,
        "score": f"{match.home_goals}:{match.away_goals}",
        "market": record.market,
        "selection": record.selection,
        "line": record.line,
        "odds": bet.odds,
        "bookmaker": record.bookmaker,
        "confidence": record.confidence,
        "p_final": round(bet.p_final, 4),
        "ev": round(bet.ev, 4),
        "outcome": record.outcome,
        "profit": round(bet.profit, 3),
        "clv": None if bet.clv is None else round(bet.clv, 4),
    }


def _equity(bets: Sequence[SimBet]) -> list[list]:
    by_day: dict[date, float] = {}
    for bet in bets:
        by_day[bet.record.day] = by_day.get(bet.record.day, 0.0) + bet.profit
    running = 0.0
    curve = []
    for day in sorted(by_day):
        running += by_day[day]
        curve.append([day.isoformat(), round(running, 3)])
    return curve


def build_report(
    run: BacktestRun,
    config: AppConfig,
    *,
    params: Params | None = None,
    sweep_rows: Sequence[SweepRow] = (),
    chosen: SweepRow | None = None,
    applied: bool = False,
    split: date | None = None,
    generated_at: datetime | None = None,
) -> dict:
    params = params or params_from_config(config)
    titles = {source.league_id: source.title for source in run.leagues}
    bets = select_bets(run.candidates, params)
    median = select_bets(run.candidates, replace(params, price="median"))
    baseline = select_bets(run.candidates, replace(params, min_ev=-1.0, min_confidence=0, max_picks=10_000))

    pyramid = {}
    for strategy, mode in PYRAMID_VARIANTS:
        bankroll = config.bankroll.model_copy(update={"mode": mode})
        attempts = simulate_pyramid(bets, bankroll, strategy)  # type: ignore[arg-type]
        pyramid[f"{strategy}_{mode}"] = pyramid_summary(attempts, bankroll.starting, titles, run.match)

    sweep_view = None
    if sweep_rows:
        ranked = sorted(sweep_rows, key=lambda r: (r.robust, r.worst_half_roi), reverse=True)
        sweep_view = {
            "split": split.isoformat() if split else None,
            "combinations": len(sweep_rows),
            "robust": sum(1 for r in sweep_rows if r.robust),
            "rows": [row_view(r) for r in ranked[:SWEEP_ROWS_SHOWN]],
            "chosen": row_view(chosen) if chosen else None,
            "applied": applied,
        }

    return {
        "generated_at": (generated_at or utcnow()).isoformat(timespec="seconds"),
        "period": {"start": run.start.isoformat(), "end": run.end.isoformat()},
        "leagues": [source.title for source in run.leagues],
        "matches": len(run.matches),
        "candidates": len(run.candidates),
        "history_matches": run.history_matches,
        "params": params_view(params),
        "summary": summarize(bets),
        "median_price": summarize(median),
        "baseline": summarize(baseline),
        "periods": {
            months: summarize([b for b in bets if b.record.day > run.end - timedelta(days=days)])
            for months, days in PERIOD_DAYS.items()
        },
        "by_market": breakdown(bets, lambda b: b.record.market),
        "by_league": breakdown(bets, lambda b: titles.get(b.record.league_id)),
        "by_confidence": breakdown(bets, confidence_bucket),
        "by_odds": breakdown(bets, odds_bucket),
        "by_bookmaker": breakdown(bets, lambda b: b.record.bookmaker),
        "by_month": breakdown(bets, lambda b: b.record.day.strftime("%Y-%m")),
        "calibration": calibration(run.candidates, params.model_weight),
        "probability_scores": probability_scores(run.matches, params.model_weight),
        "pyramid": pyramid,
        "equity": _equity(bets),
        "bets": [_bet_view(b, run, titles) for b in bets],
        "sweep": sweep_view,
        "notes": list(NOTES),
    }
