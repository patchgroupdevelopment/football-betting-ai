"""Read model for analysis results: the day's picks and per-match analysis."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from backend.database.session import Database
from backend.models import Match, Prediction
from backend.models.constants import Decision
from backend.services.leagues import league_title
from backend.services.runs import RUN_KIND_ANALYSIS, get_latest_run
from backend.utils.timeutils import to_local


@dataclass(frozen=True)
class PickView:
    prediction_id: int
    match_id: int
    rank: int | None
    decision: str
    market: str
    selection: str
    line: float | None
    odds: float | None
    bookmaker: str | None
    p_model: float | None
    p_market: float | None
    p_final: float | None
    fair_odds: float | None
    edge_pp: float | None
    ev: float | None
    confidence: int | None
    risk: str | None
    home: str
    away: str
    league: str
    kickoff_local: datetime
    status: str
    reasons: dict[str, Any] = field(default_factory=dict)

    @property
    def has_candidate(self) -> bool:
        return self.market != "NONE"

    @property
    def score(self) -> float:
        return (self.ev or 0.0) * (self.confidence or 0) / 100


@dataclass(frozen=True)
class DailyAnalysis:
    target_date: date
    analyzed: bool
    picks: tuple[PickView, ...] = ()
    watch: tuple[PickView, ...] = ()
    total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    no_bet_reasons: dict[str, int] = field(default_factory=dict)


def _view(prediction: Prediction, tz: ZoneInfo) -> PickView:
    match = prediction.match
    return PickView(
        prediction_id=prediction.id,
        match_id=prediction.match_id,
        rank=prediction.rank,
        decision=prediction.decision,
        market=prediction.market,
        selection=prediction.selection,
        line=prediction.line,
        odds=prediction.best_odds,
        bookmaker=prediction.bookmaker,
        p_model=prediction.model_prob,
        p_market=prediction.market_prob,
        p_final=prediction.final_prob,
        fair_odds=prediction.fair_odds,
        edge_pp=prediction.edge_pp,
        ev=prediction.ev,
        confidence=prediction.confidence,
        risk=prediction.risk_level,
        home=match.home_team.name,
        away=match.away_team.name,
        league=league_title(match.league),
        kickoff_local=to_local(match.kickoff_utc, tz),
        status=match.status,
        reasons=prediction.reasons or {},
    )


def _latest_predictions(session: Session, day: date) -> list[Prediction]:
    latest_ids = (
        select(func.max(Prediction.id)).where(Prediction.run_date == day).group_by(Prediction.match_id).scalar_subquery()
    )
    return list(
        session.scalars(
            select(Prediction)
            .options(
                joinedload(Prediction.match).joinedload(Match.league),
                joinedload(Prediction.match).joinedload(Match.home_team),
                joinedload(Prediction.match).joinedload(Match.away_team),
            )
            .where(Prediction.id.in_(latest_ids))
        )
    )


def latest_decisions(session: Session, day: date) -> dict[int, str]:
    """match_id -> decision of the latest prediction for the analysis day."""
    return {p.match_id: p.decision for p in _latest_predictions(session, day)}


def build_daily_analysis(db: Database, day: date, tz: ZoneInfo, *, watch_count: int = 3) -> DailyAnalysis:
    with db.session() as session:
        views = [_view(p, tz) for p in _latest_predictions(session, day)]
        ran = get_latest_run(session, day, kind=RUN_KIND_ANALYSIS) is not None
    if not views:
        return DailyAnalysis(target_date=day, analyzed=ran)

    picks = sorted((v for v in views if v.rank is not None and v.decision == Decision.BET), key=lambda v: v.rank or 0)
    watch = sorted((v for v in views if v.decision == Decision.WATCH), key=lambda v: v.score, reverse=True)
    counts = Counter(v.decision for v in views)
    reasons = Counter(
        (v.reasons.get("not_bet") or v.reasons.get("match_flags") or ["no_market_in_range"])[0]
        for v in views
        if v.decision == Decision.NO_BET
    )
    return DailyAnalysis(
        target_date=day,
        analyzed=True,
        picks=tuple(picks),
        watch=tuple(watch[:watch_count]),
        total=len(views),
        counts={d.value: counts.get(d.value, 0) for d in Decision},
        no_bet_reasons=dict(reasons.most_common()),
    )


def get_match_analysis(db: Database, match_id: int, tz: ZoneInfo) -> PickView | None:
    with db.session() as session:
        prediction = session.scalar(
            select(Prediction)
            .options(
                joinedload(Prediction.match).joinedload(Match.league),
                joinedload(Prediction.match).joinedload(Match.home_team),
                joinedload(Prediction.match).joinedload(Match.away_team),
            )
            .where(Prediction.match_id == match_id)
            .order_by(Prediction.id.desc())
            .limit(1)
        )
        return _view(prediction, tz) if prediction else None


def day_predictions(db: Database, day: date, tz: ZoneInfo) -> list[PickView]:
    """The latest prediction of every match analysed for the day, by kick-off."""
    with db.session() as session:
        views = [_view(p, tz) for p in _latest_predictions(session, day)]
    return sorted(views, key=lambda v: (v.kickoff_local, v.league, v.home))
