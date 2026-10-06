"""Data completeness score for a match (0–100).

The score tells how much of the information the model needs is actually
available. Later phases cap the confidence score with it, so a pick is never
presented as strong when the data behind it is thin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from backend.models import Lineup, Match, MatchStats, OddsSnapshot, Standing
from backend.models.constants import FINISHED_STATUSES

QualityLevel = Literal["full", "partial", "low"]

WEIGHTS: dict[str, int] = {
    "home_history": 20,  # ≥ MIN_HISTORY recent finished matches
    "away_history": 20,
    "odds": 20,  # 1X2 or goals over/under prices available
    "injuries": 10,  # injury/suspension list checked (even if empty)
    "standings": 10,
    "xg": 10,  # xG present in at least half of the recent matches
    "h2h": 5,
    "lineups": 5,  # confirmed starting line-ups (only close to kickoff)
}
MIN_HISTORY = 5
HISTORY_WINDOW = 10
FULL_THRESHOLD = 80
PARTIAL_THRESHOLD = 60


@dataclass(frozen=True)
class DataQuality:
    score: int
    level: QualityLevel
    components: dict[str, bool]


def level_for(score: int) -> QualityLevel:
    if score >= FULL_THRESHOLD:
        return "full"
    if score >= PARTIAL_THRESHOLD:
        return "partial"
    return "low"


def _recent_xg(session: Session, team_id: int, match: Match) -> list[float | None]:
    """xG of the team's recent finished matches (None where unknown), newest first.

    Results without any statistics still count as history: several free
    sources deliver only scores.
    """
    recent = list(
        session.execute(
            select(Match.id)
            .where(
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
                Match.kickoff_utc < match.kickoff_utc,
                Match.status.in_(FINISHED_STATUSES),
                Match.home_goals.is_not(None),
            )
            .order_by(Match.kickoff_utc.desc())
            .limit(HISTORY_WINDOW)
        ).scalars()
    )
    if not recent:
        return []
    xg = dict(
        session.execute(
            select(MatchStats.match_id, MatchStats.xg).where(MatchStats.team_id == team_id, MatchStats.match_id.in_(recent))
        ).all()
    )
    return [xg.get(match_id) for match_id in recent]


def _has_previous_meeting(session: Session, match: Match) -> bool:
    pair = (match.home_team_id, match.away_team_id)
    return bool(
        session.scalar(
            select(
                exists().where(
                    Match.home_team_id.in_(pair),
                    Match.away_team_id.in_(pair),
                    Match.home_team_id != Match.away_team_id,
                    Match.kickoff_utc < match.kickoff_utc,
                    Match.status.in_(FINISHED_STATUSES),
                )
            )
        )
    )


def assess_match(session: Session, match: Match) -> DataQuality:
    home_xg = _recent_xg(session, match.home_team_id, match)
    away_xg = _recent_xg(session, match.away_team_id, match)
    recent = home_xg + away_xg
    xg_coverage = sum(1 for value in recent if value is not None) / len(recent) if recent else 0.0

    components = {
        "home_history": len(home_xg) >= MIN_HISTORY,
        "away_history": len(away_xg) >= MIN_HISTORY,
        "odds": bool(
            session.scalar(
                select(
                    exists().where(OddsSnapshot.match_id == match.id, OddsSnapshot.market.in_(("1X2", "OU")))
                )
            )
        ),
        "injuries": match.injuries_checked_at is not None,
        "standings": bool(
            session.scalar(
                select(exists().where(Standing.league_id == match.league_id, Standing.season == match.season))
            )
        )
        or bool(match.league and match.league.full_results),  # table computable from complete results
        "xg": xg_coverage >= 0.5,
        "h2h": match.h2h_checked_at is not None or _has_previous_meeting(session, match),
        "lineups": (session.scalar(select(func.count(Lineup.id)).where(Lineup.match_id == match.id)) or 0) >= 2,
    }
    score = sum(WEIGHTS[name] for name, present in components.items() if present)
    return DataQuality(score=score, level=level_for(score), components=components)
