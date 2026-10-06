"""Count distributions and the Dixon-Coles score matrix."""

from __future__ import annotations

import math

MAX_GOALS = 10
Matrix = list[list[float]]


def poisson_pmf(k: int, mean: float) -> float:
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(k * math.log(mean) - mean - math.lgamma(k + 1))


def negbin_pmf(k: int, mean: float, dispersion: float) -> float:
    """Negative binomial with variance = mean × dispersion (dispersion 1.0 = Poisson).

    Corners and cards are over-dispersed; a Poisson model would be overconfident in the tails.
    """
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if dispersion <= 1.0 + 1e-9:
        return poisson_pmf(k, mean)
    r = mean / (dispersion - 1.0)
    p = r / (r + mean)
    return math.exp(
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1.0 - p)
    )


def count_over_probability(mean: float, line: float, dispersion: float = 1.0) -> float:
    """P(X > line) for a half line (e.g. 9.5 -> X ≥ 10)."""
    threshold = math.floor(line)
    cdf = sum(negbin_pmf(k, mean, dispersion) for k in range(threshold + 1))
    return min(1.0, max(0.0, 1.0 - cdf))


def dixon_coles_tau(home_goals: int, away_goals: int, lambda_home: float, lambda_away: float, rho: float) -> float:
    """Low-score correction: plain Poisson misprices 0-0, 1-0, 0-1 and 1-1."""
    if home_goals == 0 and away_goals == 0:
        return 1.0 - lambda_home * lambda_away * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + lambda_home * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + lambda_away * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(lambda_home: float, lambda_away: float, rho: float = 0.0, max_goals: int = MAX_GOALS) -> Matrix:
    """matrix[x][y] = P(home scores x, away scores y), normalised to sum to 1."""
    home = [poisson_pmf(x, lambda_home) for x in range(max_goals + 1)]
    away = [poisson_pmf(y, lambda_away) for y in range(max_goals + 1)]
    matrix = [
        [max(0.0, dixon_coles_tau(x, y, lambda_home, lambda_away, rho)) * home[x] * away[y] for y in range(max_goals + 1)]
        for x in range(max_goals + 1)
    ]
    total = sum(sum(row) for row in matrix)
    return [[cell / total for cell in row] for row in matrix] if total > 0 else matrix
