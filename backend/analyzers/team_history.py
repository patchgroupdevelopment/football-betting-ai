"""Finished matches from one team's perspective, and raw match lists for model fitting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.models import Match, MatchStats
from backend.models.constants import FINISHED_STATUSES

Outcome = Literal["W", "D", "L"]


@dataclass(frozen=True)
class TeamMatch:
    match_id: int
    kickoff_utc: datetime
    league_id: int
    is_home: bool
    opponent_id: int
    goals_for: int
    goals_against: int
    ht_for: int | None = None
    ht_against: int | None = None
    xg_for: float | None = None
    xg_against: float | None = None
    corners_for: int | None = None
    corners_against: int | None = None
    cards_for: int | None = None
    cards_against: int | None = None

    @property
    def outcome(self) -> Outcome:
        if self.goals_for > self.goals_against:
            return "W"
        return "D" if self.goals_for == self.goals_against else "L"

    @property
    def points(self) -> int:
        return {"W": 3, "D": 1, "L": 0}[self.outcome]

    @property
    def total_goals(self) -> int:
        return self.goals_for + self.goals_against


@dataclass(frozen=True)
class FinishedMatch:
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int
    ht_home_goals: int | None
    ht_away_goals: int | None
    home_xg: float | None
    away_xg: float | None
    kickoff_utc: datetime


def _cards(row: MatchStats | None) -> int | None:
    if row is None or row.yellow_cards is None:
        return None
    return (row.yellow_cards or 0) + (row.red_cards or 0)


def _stats_by_match(session: Session, match_ids: list[int]) -> dict[tuple[int, int], MatchStats]:
    if not match_ids:
        return {}
    rows = session.scalars(select(MatchStats).where(MatchStats.match_id.in_(match_ids)))
    return {(row.match_id, row.team_id): row for row in rows}


def to_team_match(match: Match, team_id: int, stats: dict[tuple[int, int], MatchStats]) -> TeamMatch:
    is_home = match.home_team_id == team_id
    opponent_id = match.away_team_id if is_home else match.home_team_id
    own = stats.get((match.id, team_id))
    other = stats.get((match.id, opponent_id))
    goals_for = match.home_goals if is_home else match.away_goals
    goals_against = match.away_goals if is_home else match.home_goals
    ht_for = match.ht_home_goals if is_home else match.ht_away_goals
    ht_against = match.ht_away_goals if is_home else match.ht_home_goals
    return TeamMatch(
        match_id=match.id,
        kickoff_utc=match.kickoff_utc,
        league_id=match.league_id,
        is_home=is_home,
        opponent_id=opponent_id,
        goals_for=goals_for or 0,
        goals_against=goals_against or 0,
        ht_for=ht_for,
        ht_against=ht_against,
        xg_for=own.xg if own else None,
        xg_against=other.xg if other else None,
        corners_for=own.corners if own else None,
        corners_against=other.corners if other else None,
        cards_for=_cards(own),
        cards_against=_cards(other),
    )


def load_team_history(session: Session, team_id: int, before: datetime, limit: int = 20) -> list[TeamMatch]:
    """Finished matches of a team before ``before``, newest first."""
    matches = list(
        session.scalars(
            select(Match)
            .where(
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
                Match.status.in_(FINISHED_STATUSES),
                Match.kickoff_utc < before,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            )
            .order_by(Match.kickoff_utc.desc())
            .limit(limit)
        )
    )
    stats = _stats_by_match(session, [m.id for m in matches])
    return [to_team_match(m, team_id, stats) for m in matches]


def load_finished_matches(session: Session, since: datetime, until: datetime) -> list[FinishedMatch]:
    """All finished matches in [since, until), oldest first — input for Elo and Dixon-Coles."""
    matches = list(
        session.scalars(
            select(Match)
            .where(
                Match.status.in_(FINISHED_STATUSES),
                Match.kickoff_utc >= since,
                Match.kickoff_utc < until,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            )
            .order_by(Match.kickoff_utc, Match.id)
        )
    )
    stats = _stats_by_match(session, [m.id for m in matches])
    result: list[FinishedMatch] = []
    for m in matches:
        home = stats.get((m.id, m.home_team_id))
        away = stats.get((m.id, m.away_team_id))
        result.append(
            FinishedMatch(
                home_id=m.home_team_id,
                away_id=m.away_team_id,
                home_goals=m.home_goals or 0,
                away_goals=m.away_goals or 0,
                ht_home_goals=m.ht_home_goals,
                ht_away_goals=m.ht_away_goals,
                home_xg=home.xg if home else None,
                away_xg=away.xg if away else None,
                kickoff_utc=m.kickoff_utc,
            )
        )
    return result
