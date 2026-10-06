"""Team attack/defence ratings for a Dixon-Coles goal model.

Model: λ_home = base × home_adv × attack[home] × defence[away]
       λ_away = base × attack[away] × defence[home]

Ratings are fitted by iterative proportional fitting, which solves the
(time-weighted) Poisson maximum-likelihood equations for this multiplicative
model without a numerical optimiser. Two additions keep it robust on sparse
data:
- each team gets ``prior_matches`` virtual matches at average strength, so a
  team seen twice is pulled towards 1.0 instead of taking extreme values;
- the goal target blends real goals with xG (``xg_weight``) when both teams'
  xG is known, which reduces noise from finishing luck.
Old matches are down-weighted exponentially (``half_life_days``).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.predictors.poisson import dixon_coles_tau

DEFAULT_BASE = 1.25
DEFAULT_HOME_ADVANTAGE = 1.20
MIN_MATCHES_FOR_RHO = 300


@dataclass(frozen=True)
class MatchObservation:
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int
    home_xg: float | None
    away_xg: float | None
    age_days: float


@dataclass(frozen=True)
class TeamRatings:
    attack: dict[int, float] = field(default_factory=dict)
    defence: dict[int, float] = field(default_factory=dict)
    base: float = DEFAULT_BASE
    home_advantage: float = DEFAULT_HOME_ADVANTAGE
    rho: float = -0.05
    observations: dict[int, int] = field(default_factory=dict)

    def expected_goals(self, home_id: int, away_id: int) -> tuple[float, float]:
        lambda_home = self.base * self.home_advantage * self.attack.get(home_id, 1.0) * self.defence.get(away_id, 1.0)
        lambda_away = self.base * self.attack.get(away_id, 1.0) * self.defence.get(home_id, 1.0)
        return lambda_home, lambda_away


def _target_goals(obs: MatchObservation, xg_weight: float) -> tuple[float, float]:
    if obs.home_xg is None or obs.away_xg is None:
        return float(obs.home_goals), float(obs.away_goals)
    return (
        (1 - xg_weight) * obs.home_goals + xg_weight * obs.home_xg,
        (1 - xg_weight) * obs.away_goals + xg_weight * obs.away_xg,
    )


def fit_ratings(
    observations: Sequence[MatchObservation],
    *,
    xg_weight: float = 0.6,
    half_life_days: float = 180.0,
    prior_matches: float = 3.0,
    default_rho: float = -0.05,
    max_iterations: int = 200,
    tolerance: float = 1e-7,
) -> TeamRatings:
    if not observations:
        return TeamRatings(rho=default_rho)

    decay = math.log(2) / half_life_days
    rows: list[tuple[int, int, float, float, float]] = []
    for obs in observations:
        home_target, away_target = _target_goals(obs, xg_weight)
        rows.append((obs.home_id, obs.away_id, home_target, away_target, math.exp(-decay * max(0.0, obs.age_days))))

    total_weight = sum(r[4] for r in rows)
    mean_home = sum(r[2] * r[4] for r in rows) / total_weight
    mean_away = sum(r[3] * r[4] for r in rows) / total_weight
    base = mean_away if mean_away > 0 else DEFAULT_BASE
    home_advantage = min(1.6, max(1.0, mean_home / mean_away)) if mean_away > 0 else DEFAULT_HOME_ADVANTAGE

    counts: dict[int, int] = defaultdict(int)
    for home, away, *_ in rows:
        counts[home] += 1
        counts[away] += 1
    teams = list(counts)
    attack = dict.fromkeys(teams, 1.0)
    defence = dict.fromkeys(teams, 1.0)
    prior_goals = prior_matches * base * (1 + home_advantage) / 2

    for _ in range(max_iterations):
        numerator = dict.fromkeys(teams, prior_goals)
        denominator = dict.fromkeys(teams, prior_goals)
        for home, away, home_goals, away_goals, weight in rows:
            numerator[home] += weight * home_goals
            denominator[home] += weight * base * home_advantage * defence[away]
            numerator[away] += weight * away_goals
            denominator[away] += weight * base * defence[home]
        new_attack = {t: numerator[t] / denominator[t] for t in teams}

        numerator = dict.fromkeys(teams, prior_goals)
        denominator = dict.fromkeys(teams, prior_goals)
        for home, away, home_goals, away_goals, weight in rows:
            numerator[away] += weight * home_goals  # goals conceded by the away team
            denominator[away] += weight * base * home_advantage * new_attack[home]
            numerator[home] += weight * away_goals
            denominator[home] += weight * base * new_attack[away]
        new_defence = {t: numerator[t] / denominator[t] for t in teams}

        change = max(
            max(abs(new_attack[t] - attack[t]) for t in teams),
            max(abs(new_defence[t] - defence[t]) for t in teams),
        )
        attack, defence = new_attack, new_defence
        if change < tolerance:
            break

    ratings = TeamRatings(attack, defence, base, home_advantage, default_rho, dict(counts))
    if len(observations) >= MIN_MATCHES_FOR_RHO:
        ratings = TeamRatings(attack, defence, base, home_advantage, _fit_rho(observations, ratings, decay), dict(counts))
    return ratings


def _fit_rho(observations: Sequence[MatchObservation], ratings: TeamRatings, decay: float) -> float:
    """1-D grid search of the low-score correction on actual (integer) scores."""
    low_scores = []
    for obs in observations:
        if obs.home_goals <= 1 and obs.away_goals <= 1:
            lambda_home, lambda_away = ratings.expected_goals(obs.home_id, obs.away_id)
            low_scores.append((obs.home_goals, obs.away_goals, lambda_home, lambda_away, math.exp(-decay * obs.age_days)))
    if not low_scores:
        return ratings.rho

    def log_likelihood(rho: float) -> float:
        total = 0.0
        for x, y, lambda_home, lambda_away, weight in low_scores:
            tau = dixon_coles_tau(x, y, lambda_home, lambda_away, rho)
            if tau <= 0:
                return -math.inf
            total += weight * math.log(tau)
        return total

    grid = [round(-0.20 + 0.01 * i, 2) for i in range(31)]
    return max(grid, key=log_likelihood)
