"""Football Elo ratings (goal-difference weighted, with home advantage)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class EloSettings:
    start: float = 1500.0
    k: float = 20.0
    home_advantage: float = 65.0


def expected_score(rating_home: float, rating_away: float, home_advantage: float) -> float:
    """Expected score of the home team (win = 1, draw = 0.5)."""
    return 1.0 / (1.0 + 10 ** ((rating_away - rating_home - home_advantage) / 400.0))


def goal_difference_multiplier(goal_difference: int) -> float:
    margin = abs(goal_difference)
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11 + margin) / 8


def compute_ratings(
    matches: Iterable[tuple[int, int, int, int]], settings: EloSettings = EloSettings()
) -> dict[int, float]:
    """``matches`` = (home_id, away_id, home_goals, away_goals) in chronological order."""
    ratings: dict[int, float] = {}
    for home, away, home_goals, away_goals in matches:
        rating_home = ratings.get(home, settings.start)
        rating_away = ratings.get(away, settings.start)
        expected = expected_score(rating_home, rating_away, settings.home_advantage)
        actual = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
        delta = settings.k * goal_difference_multiplier(home_goals - away_goals) * (actual - expected)
        ratings[home] = rating_home + delta
        ratings[away] = rating_away - delta
    return ratings


def outcome_probabilities(rating_home: float, rating_away: float, home_advantage: float) -> tuple[float, float, float]:
    """Home/draw/away probabilities from the Elo expected score.

    Elo only yields an expected score E = P(home) + P(draw)/2; the draw share
    shrinks as the mismatch grows (27 % for equal teams, ~18 % at E = 0.8).
    """
    expected = expected_score(rating_home, rating_away, home_advantage)
    draw = min(0.30, max(0.12, 0.27 - 0.25 * (2 * expected - 1) ** 2))
    home = max(0.01, expected - draw / 2)
    away = max(0.01, 1.0 - home - draw)
    total = home + draw + away
    return home / total, draw / total, away / total
