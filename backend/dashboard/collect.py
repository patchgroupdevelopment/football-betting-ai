"""Everything the dashboard shows, as plain JSON with Azerbaijani display texts already rendered.

The page itself only lays the data out; no key or token ever reaches it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from backend.backtest.service import latest_report
from backend.config import AppConfig
from backend.database.session import Database
from backend.i18n.az import DASHBOARD, DECISION_ICONS, DECISIONS, MARKETS, PYRAMID_MODES, RISK_LEVELS
from backend.models import Bet, Match, ModelRun, OddsSnapshot, Prediction, PyramidStage, Team
from backend.models.constants import BetStatus, Decision
from backend.presenters.formatters import min_odds
from backend.presenters.labels import reasons_text, selection_label
from backend.services.picks import PickView, day_predictions
from backend.services.price_checks import bookmaker_stats
from backend.services.pyramid import PyramidService
from backend.services.results import TrackRecord, track_record
from backend.services.runs import RUN_KIND_ANALYSIS, RUN_KIND_DAILY
from backend.utils.formatting import format_date, format_date_with_weekday, format_datetime, format_time
from backend.utils.timeutils import local_now, to_local

SETTLED_SHOWN = 150
BET_STATUS = {
    BetStatus.WON: "won",
    BetStatus.LOST: "lost",
    BetStatus.VOID: "void",
    BetStatus.PENDING: "pending",
}


def _pct(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value * 100, digits)


def _pick(view: PickView) -> dict[str, Any]:
    has = view.has_candidate
    reasons = view.reasons or {}
    return {
        "match_id": view.match_id,
        "rank": view.rank,
        "league": view.league,
        "home": view.home,
        "away": view.away,
        "time": format_time(view.kickoff_local),
        "decision": view.decision,
        "decision_label": f"{DECISION_ICONS.get(view.decision, '')} {DECISIONS.get(view.decision, view.decision)}".strip(),
        "pick": selection_label(view.market, view.selection, view.line, view.home, view.away) if has else None,
        "market": MARKETS.get(view.market) if has else None,
        "odds": view.odds,
        "bookmaker": view.bookmaker,
        "p_final": _pct(view.p_final),
        "p_market": _pct(view.p_market),
        "p_model": _pct(view.p_model),
        "fair_odds": round(view.fair_odds, 2) if view.fair_odds else None,
        "min_odds": min_odds(view) if has else None,
        "ev": _pct(view.ev),
        "confidence": view.confidence,
        "risk": view.risk,
        "risk_label": RISK_LEVELS.get(view.risk or "", None),
        "why": list(reasons.get("why") or [])[:5],
        "against": list(reasons.get("against") or [])[:4],
        "reason": reasons_text(reasons.get("not_bet") or reasons.get("match_flags") or [])
        if view.decision != Decision.BET
        else None,
    }


def _today(db: Database, day: date, tz: ZoneInfo) -> dict[str, Any]:
    views = day_predictions(db, day, tz)
    picks = sorted((v for v in views if v.rank is not None and v.decision == Decision.BET), key=lambda v: v.rank or 0)
    watch = sorted((v for v in views if v.decision == Decision.WATCH), key=lambda v: v.score, reverse=True)
    counts = {d.value: sum(1 for v in views if v.decision == d) for d in Decision}
    return {
        "date": format_date_with_weekday(day),
        "total": len(views),
        "counts": counts,
        "picks": [_pick(v) for v in picks],
        "watch": [_pick(v) for v in watch[:5]],
        "matches": [_pick(v) for v in views],
    }


def _record(record: TrackRecord) -> dict[str, Any]:
    return {
        "bets": record.bets,
        "wins": record.wins,
        "losses": record.losses,
        "voids": record.voids,
        "pending": record.pending,
        "pnl": record.pnl,
        "hit_rate": _pct(record.hit_rate),
        "roi": _pct(record.roi),
        "avg_odds": round(record.avg_odds, 2) if record.avg_odds else None,
        "clv": _pct(record.avg_clv),
    }


def _results(db: Database, day: date, tz: ZoneInfo) -> dict[str, Any]:
    with db.session() as session:
        rows = session.execute(
            select(Bet, Prediction)
            .join(Prediction, Prediction.id == Bet.prediction_id)
            .options(joinedload(Prediction.match).joinedload(Match.home_team), joinedload(Prediction.match).joinedload(Match.away_team))
            .where(Bet.is_paper.is_(True))
            .order_by(Bet.id)
        ).all()
        bets = []
        equity: dict[str, float] = {}
        running = 0.0
        for bet, prediction in rows:
            match = prediction.match
            settled = bet.status != BetStatus.PENDING
            when = bet.settled_at or match.kickoff_utc
            if settled:
                running += bet.pnl or 0.0
                equity[to_local(when, tz).date().isoformat()] = round(running, 3)
            bets.append(
                {
                    "date": format_date(prediction.run_date) if prediction.run_date else "",
                    "rank": prediction.rank,
                    "home": match.home_team.name,
                    "away": match.away_team.name,
                    "score": f"{match.home_goals}:{match.away_goals}" if match.home_goals is not None else None,
                    "pick": selection_label(prediction.market, prediction.selection, prediction.line, match.home_team.name, match.away_team.name),
                    "odds": bet.odds_taken,
                    "status": BET_STATUS.get(BetStatus(bet.status), bet.status),
                    "pnl": bet.pnl,
                    "clv": _pct(bet.clv),
                }
            )
    return {
        "overall": _record(track_record(db)),
        "top": _record(track_record(db, top_only=True)),
        "recent": _record(track_record(db, since=day - timedelta(days=30))),
        "equity": [[k, v] for k, v in sorted(equity.items())],
        "bets": list(reversed(bets))[:SETTLED_SHOWN],
    }


def _prices(db: Database, config: AppConfig) -> dict[str, Any] | None:
    bookmaker = config.selection.user_bookmaker
    if not bookmaker:
        return None
    stats = bookmaker_stats(db, bookmaker)
    return {
        "bookmaker": bookmaker,
        "checks": stats.checks,
        "passed": stats.passed,
        "gap_fair": _pct(stats.gap_to_fair),
        "gap_best": _pct(stats.gap_to_best),
        "passed_roi": _pct(stats.passed_results.roi),
        "passed_bets": stats.passed_results.bets,
        "all_roi": _pct(stats.all_results.roi),
        "all_bets": stats.all_results.bets,
    }


def _pyramid(db: Database, config: AppConfig) -> dict[str, Any]:
    service = PyramidService(db, config.bankroll)
    state = service.get_state()
    projection = service.projection(state)
    with db.session() as session:
        stages = [
            {
                "attempt": s.attempt_no,
                "stage": s.stage_no,
                "before": s.balance_before,
                "odds": s.odds,
                "after": s.balance_after,
                "locked": s.locked_amount,
                "status": s.status,
            }
            for s in session.scalars(select(PyramidStage).order_by(PyramidStage.id.desc()).limit(60))
        ]
    return {
        "mode": PYRAMID_MODES.get(state.mode, state.mode),
        "attempt": state.attempt_no,
        "stage": state.stage_no,
        "balance": state.balance,
        "starting": state.starting,
        "target": state.target,
        "locked": state.locked_total,
        "pending_odds": state.pending_odds,
        "total_staked": state.total_staked,
        "stages_needed": projection.stages_needed,
        "projection_odds": projection.odds,
        "stages": stages,
    }


def _backtest(db: Database) -> dict[str, Any] | None:
    report = latest_report(db)
    if not report:
        return None
    for bet in report.get("bets", []):
        bet["pick"] = selection_label(bet["market"], bet["selection"], bet["line"], bet["home"], bet["away"])
    for variant in (report.get("pyramid") or {}).values():
        for stage in (variant.get("max_attempt") or {}).get("path", []):
            stage["pick"] = selection_label(stage["market"], stage["selection"], stage["line"], stage["home"], stage["away"])
        for attempt in variant.get("attempt_list", []):
            broke = attempt.get("broke_at")
            if broke:
                broke["pick"] = selection_label(broke["market"], broke["selection"], broke["line"], broke["home"], broke["away"])
    report["market_names"] = {k: MARKETS.get(k, k) for k in (report.get("by_market") or {})}
    return report


def _system(db: Database, tz: ZoneInfo) -> dict[str, Any]:
    with db.session() as session:
        runs = []
        for kind in (RUN_KIND_DAILY, RUN_KIND_ANALYSIS, "backtest"):
            run = session.scalar(select(ModelRun).where(ModelRun.kind == kind).order_by(ModelRun.id.desc()).limit(1))
            if run is not None:
                finished = run.finished_at or run.started_at
                runs.append({"kind": kind, "status": run.status, "when": format_datetime(to_local(finished, tz))})
        return {
            "runs": runs,
            "matches": session.scalar(select(func.count(Match.id))) or 0,
            "teams": session.scalar(select(func.count(Team.id))) or 0,
            "odds": session.scalar(select(func.count(OddsSnapshot.id))) or 0,
        }


def collect(db: Database, config: AppConfig, tz: ZoneInfo, day: date, *, paper_mode: bool) -> dict[str, Any]:
    return {
        "labels": DASHBOARD,
        "generated": format_datetime(local_now(tz)),
        "paper_mode": paper_mode,
        "user_bookmaker": config.selection.user_bookmaker,
        "today": _today(db, day, tz),
        "results": _results(db, day, tz),
        "prices": _prices(db, config),
        "pyramid": _pyramid(db, config),
        "backtest": _backtest(db),
        "system": _system(db, tz),
    }
