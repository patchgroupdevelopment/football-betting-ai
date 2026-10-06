"""Motivation from the league table and the competition stage.

Levels are 0–100 (60 = normal). Tags explain the level and are rendered in
Azerbaijani by the presenters.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Match, Standing
from backend.models.constants import FINISHED_STATUSES

KNOCKOUT_MARKERS = ("final", "semi", "quarter", "round of", "knockout", "play-off", "playoff", "1/8", "1/16")
NEUTRAL_LEVEL = 60


@dataclass(frozen=True)
class StandingInfo:
    rank: int
    points: int
    played: int
    group_size: int
    gap_to_top: int
    gap_to_safety: int | None  # points above the first relegation place (≤ 0 = in the zone)
    description: str | None
    form: str | None


@dataclass(frozen=True)
class MotivationInfo:
    level: int
    tags: tuple[str, ...]
    standing: StandingInfo | None = None


def load_standing(session: Session, league_id: int, season: int, team_id: int) -> StandingInfo | None:
    row = session.scalar(
        select(Standing).where(Standing.league_id == league_id, Standing.season == season, Standing.team_id == team_id)
    )
    if row is None or row.rank is None or row.points is None:
        return None
    group = list(
        session.scalars(
            select(Standing)
            .where(Standing.league_id == league_id, Standing.season == season, Standing.group_name == row.group_name)
            .order_by(Standing.rank)
        )
    )
    size = len(group)
    top_points = max((r.points or 0) for r in group)
    gap_to_safety: int | None = None
    if size >= 10:
        first_relegated = next((r for r in group if r.rank == size - 2), None)
        if first_relegated is not None and first_relegated.points is not None:
            gap_to_safety = row.points - first_relegated.points
    return StandingInfo(
        rank=row.rank,
        points=row.points,
        played=row.played or 0,
        group_size=size,
        gap_to_top=top_points - row.points,
        gap_to_safety=gap_to_safety,
        description=row.description,
        form=row.form,
    )


Table = dict[int, list[int]]  # team -> [points, played, goal difference]


def add_result(table: Table, home: int, away: int, home_goals: int, away_goals: int) -> None:
    for team, scored, conceded in ((home, home_goals, away_goals), (away, away_goals, home_goals)):
        entry = table.setdefault(team, [0, 0, 0])
        entry[0] += 3 if scored > conceded else 1 if scored == conceded else 0
        entry[1] += 1
        entry[2] += scored - conceded


def table_standing(table: Table, team_id: int) -> StandingInfo | None:
    if team_id not in table or len(table) < 6:
        return None
    ranked = sorted(table, key=lambda t: (table[t][0], table[t][2]), reverse=True)
    size = len(ranked)
    points = table[team_id][0]
    gap_to_safety = points - table[ranked[size - 3]][0] if size >= 10 else None
    return StandingInfo(
        rank=ranked.index(team_id) + 1,
        points=points,
        played=table[team_id][1],
        group_size=size,
        gap_to_top=table[ranked[0]][0] - points,
        gap_to_safety=gap_to_safety,
        description=None,
        form=None,
    )


def computed_standing(session: Session, league_id: int, season: int, team_id: int) -> StandingInfo | None:
    """A league table rebuilt from stored results, for leagues without an official standings feed.

    Only meaningful when the league's complete results are stored (``League.full_results``).
    """
    rows = session.execute(
        select(Match.home_team_id, Match.away_team_id, Match.home_goals, Match.away_goals).where(
            Match.league_id == league_id,
            Match.season == season,
            Match.status.in_(FINISHED_STATUSES),
            Match.home_goals.is_not(None),
        )
    ).all()
    table: Table = {}
    for home, away, home_goals, away_goals in rows:
        add_result(table, home, away, home_goals, away_goals)
    return table_standing(table, team_id)


def is_knockout(round_name: str | None) -> bool:
    text = (round_name or "").lower()
    return any(marker in text for marker in KNOCKOUT_MARKERS)


def motivation_info(standing: StandingInfo | None, round_name: str | None) -> MotivationInfo:
    if is_knockout(round_name):
        return MotivationInfo(85, ("cup_knockout",), standing)
    if standing is None:
        return MotivationInfo(NEUTRAL_LEVEL, ("unknown",), None)
    if standing.played < 5:
        return MotivationInfo(NEUTRAL_LEVEL, ("early_season",), standing)

    remaining = max(0, 2 * (standing.group_size - 1) - standing.played)
    description = (standing.description or "").lower()
    levels: dict[str, int] = {}
    if standing.rank <= 3 and standing.gap_to_top <= max(3, remaining // 2):
        levels["title_race"] = 85
    if standing.gap_to_safety is not None and (standing.gap_to_safety <= 3 or standing.rank >= standing.group_size - 2):
        levels["relegation_battle"] = 90
    if any(word in description for word in ("champions league", "europa", "conference")) and "title_race" not in levels:
        levels["europe_race"] = 75
    if not levels:
        levels["safe_midtable" if remaining <= 8 else "regular"] = 45 if remaining <= 8 else NEUTRAL_LEVEL
    tags = tuple(sorted(levels, key=levels.get, reverse=True))
    return MotivationInfo(max(levels.values()), tags, standing)


def motivation_multiplier(info: MotivationInfo, max_effect: float) -> float:
    """Small attack multiplier: +max_effect at level 90, about −max_effect/2 at level 45."""
    return 1.0 + max_effect * (info.level - NEUTRAL_LEVEL) / 30
