"""Form statistics over a list of a team's matches (last 5 / last 10, all or home/away only)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import fmean

from backend.analyzers.team_history import TeamMatch

MIN_XG_COVERAGE = 0.5


@dataclass(frozen=True)
class FormStats:
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    points: int = 0
    goals_for_avg: float = 0.0
    goals_against_avg: float = 0.0
    clean_sheet_rate: float = 0.0
    failed_to_score_rate: float = 0.0
    btts_rate: float = 0.0
    over15_rate: float = 0.0
    over25_rate: float = 0.0
    first_half_goal_rate: float | None = None
    xg_for_avg: float | None = None
    xg_against_avg: float | None = None
    corners_for_avg: float | None = None
    corners_against_avg: float | None = None
    cards_for_avg: float | None = None
    cards_against_avg: float | None = None
    sequence: str = ""  # newest first, W/D/L

    @property
    def ppg(self) -> float:
        return self.points / self.matches if self.matches else 0.0

    @property
    def under25_rate(self) -> float:
        return 1.0 - self.over25_rate if self.matches else 0.0


def rate(matches: Sequence[TeamMatch], predicate: Callable[[TeamMatch], bool | None]) -> float | None:
    """Share of matches where ``predicate`` is true; matches where it is None (missing data) are skipped."""
    values = [predicate(m) for m in matches]
    known = [v for v in values if v is not None]
    return sum(1 for v in known if v) / len(known) if known else None


def _avg(values: list[float | int | None], minimum_share: float = 0.0, total: int = 0) -> float | None:
    known = [float(v) for v in values if v is not None]
    if not known or (total and len(known) / total < minimum_share):
        return None
    return fmean(known)


def form_stats(matches: Sequence[TeamMatch]) -> FormStats:
    n = len(matches)
    if n == 0:
        return FormStats()
    wins = sum(1 for m in matches if m.outcome == "W")
    draws = sum(1 for m in matches if m.outcome == "D")
    return FormStats(
        matches=n,
        wins=wins,
        draws=draws,
        losses=n - wins - draws,
        points=sum(m.points for m in matches),
        goals_for_avg=fmean(m.goals_for for m in matches),
        goals_against_avg=fmean(m.goals_against for m in matches),
        clean_sheet_rate=sum(1 for m in matches if m.goals_against == 0) / n,
        failed_to_score_rate=sum(1 for m in matches if m.goals_for == 0) / n,
        btts_rate=sum(1 for m in matches if m.goals_for > 0 and m.goals_against > 0) / n,
        over15_rate=sum(1 for m in matches if m.total_goals > 1.5) / n,
        over25_rate=sum(1 for m in matches if m.total_goals > 2.5) / n,
        first_half_goal_rate=rate(
            matches, lambda m: None if m.ht_for is None or m.ht_against is None else m.ht_for + m.ht_against > 0
        ),
        xg_for_avg=_avg([m.xg_for for m in matches], MIN_XG_COVERAGE, n),
        xg_against_avg=_avg([m.xg_against for m in matches], MIN_XG_COVERAGE, n),
        corners_for_avg=_avg([m.corners_for for m in matches]),
        corners_against_avg=_avg([m.corners_against for m in matches]),
        cards_for_avg=_avg([m.cards_for for m in matches]),
        cards_against_avg=_avg([m.cards_against for m in matches]),
        sequence="".join(m.outcome for m in matches),
    )


def total_goals_over_rate(matches: Sequence[TeamMatch], line: float) -> float | None:
    return rate(matches, lambda m: m.total_goals > line)


def team_goals_over_rate(matches: Sequence[TeamMatch], line: float, *, conceded: bool = False) -> float | None:
    return rate(matches, lambda m: (m.goals_against if conceded else m.goals_for) > line)


def first_half_over_rate(matches: Sequence[TeamMatch], line: float) -> float | None:
    return rate(matches, lambda m: None if m.ht_for is None or m.ht_against is None else m.ht_for + m.ht_against > line)


def btts_rate(matches: Sequence[TeamMatch]) -> float | None:
    return rate(matches, lambda m: m.goals_for > 0 and m.goals_against > 0)


def corners_over_rate(matches: Sequence[TeamMatch], line: float) -> float | None:
    return rate(
        matches,
        lambda m: None if m.corners_for is None or m.corners_against is None else m.corners_for + m.corners_against > line,
    )


def cards_over_rate(matches: Sequence[TeamMatch], line: float) -> float | None:
    return rate(
        matches, lambda m: None if m.cards_for is None or m.cards_against is None else m.cards_for + m.cards_against > line
    )
