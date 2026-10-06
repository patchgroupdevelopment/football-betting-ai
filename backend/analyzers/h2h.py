"""Head-to-head summary from the home team's perspective."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.analyzers.team_history import TeamMatch, to_team_match
from backend.models import Match
from backend.models.constants import FINISHED_STATUSES


@dataclass(frozen=True)
class H2HSummary:
    matches: tuple[TeamMatch, ...] = ()  # home team's perspective, newest first

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def home_wins(self) -> int:
        return sum(1 for m in self.matches if m.outcome == "W")

    @property
    def draws(self) -> int:
        return sum(1 for m in self.matches if m.outcome == "D")

    @property
    def away_wins(self) -> int:
        return sum(1 for m in self.matches if m.outcome == "L")

    @property
    def avg_goals(self) -> float | None:
        return fmean(m.total_goals for m in self.matches) if self.matches else None

    def rate(self, predicate: Callable[[TeamMatch], bool]) -> float | None:
        return sum(1 for m in self.matches if predicate(m)) / self.count if self.matches else None


def load_h2h(session: Session, home_id: int, away_id: int, before: datetime, limit: int = 10) -> H2HSummary:
    matches = list(
        session.scalars(
            select(Match)
            .where(
                or_(
                    and_(Match.home_team_id == home_id, Match.away_team_id == away_id),
                    and_(Match.home_team_id == away_id, Match.away_team_id == home_id),
                ),
                Match.status.in_(FINISHED_STATUSES),
                Match.kickoff_utc < before,
                Match.home_goals.is_not(None),
            )
            .order_by(Match.kickoff_utc.desc())
            .limit(limit)
        )
    )
    return H2HSummary(tuple(to_team_match(m, home_id, {}) for m in matches))
