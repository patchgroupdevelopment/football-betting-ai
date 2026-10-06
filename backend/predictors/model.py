"""Fits the models once per analysis run and builds the goal model for each match."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from statistics import fmean

from backend.analyzers.context import MatchContext, TeamContext
from backend.analyzers.fatigue import fatigue_multiplier
from backend.analyzers.motivation import motivation_multiplier
from backend.analyzers.team_history import FinishedMatch, TeamMatch
from backend.config import ModelConfig
from backend.predictors.dixon_coles import MatchObservation, TeamRatings, fit_ratings
from backend.predictors.elo import EloSettings, compute_ratings, expected_score, outcome_probabilities
from backend.predictors.markets import GoalModel

DEFAULT_FIRST_HALF_FRACTION = 0.44
MIN_COUNT_MATCHES = 5  # corners/cards need this many matches with data per team


@dataclass(frozen=True)
class FittedModels:
    ratings: TeamRatings
    elo: dict[int, float]
    elo_settings: EloSettings = field(default_factory=EloSettings)
    first_half_fraction: float = DEFAULT_FIRST_HALF_FRACTION
    global_cards_avg: float | None = None
    matches_used: int = 0

    def elo_of(self, team_id: int) -> float:
        return self.elo.get(team_id, self.elo_settings.start)


@dataclass(frozen=True)
class Adjustment:
    key: str  # "injuries" | "fatigue" | "motivation"
    team: str  # "home" | "away"
    target: str  # "attack" | "defence"
    multiplier: float


@dataclass(frozen=True)
class MatchModel:
    goal_model: GoalModel
    base_lambdas: tuple[float, float]
    lambdas: tuple[float, float]
    adjustments: tuple[Adjustment, ...]
    elo_home: float
    elo_away: float
    elo_expected_home: float  # Elo expected score of the home team

    @property
    def total_goals(self) -> float:
        return self.lambdas[0] + self.lambdas[1]


def fit_models(matches: Sequence[FinishedMatch], now: datetime, cfg: ModelConfig) -> FittedModels:
    observations = [
        MatchObservation(
            m.home_id, m.away_id, m.home_goals, m.away_goals, m.home_xg, m.away_xg,
            age_days=(now - m.kickoff_utc).total_seconds() / 86_400,
        )
        for m in matches
    ]
    ratings = fit_ratings(
        observations, xg_weight=cfg.xg_weight, half_life_days=cfg.half_life_days, prior_matches=cfg.prior_matches
    )
    elo = compute_ratings((m.home_id, m.away_id, m.home_goals, m.away_goals) for m in matches)

    with_halftime = [m for m in matches if m.ht_home_goals is not None and m.ht_away_goals is not None]
    full = sum(m.home_goals + m.away_goals for m in with_halftime)
    first = sum((m.ht_home_goals or 0) + (m.ht_away_goals or 0) for m in with_halftime)
    fraction = min(0.50, max(0.38, first / full)) if full >= 50 else DEFAULT_FIRST_HALF_FRACTION
    return FittedModels(ratings=ratings, elo=elo, first_half_fraction=fraction, matches_used=len(matches))


def _count_with(history: Sequence[TeamMatch], attribute: str) -> int:
    return sum(1 for m in history if getattr(m, attribute) is not None)


def corners_mean(ctx: MatchContext) -> float | None:
    home, away = ctx.home, ctx.away
    if min(_count_with(home.history[:10], "corners_for"), _count_with(away.history[:10], "corners_for")) < MIN_COUNT_MATCHES:
        return None
    h, a = home.form10, away.form10
    if None in (h.corners_for_avg, h.corners_against_avg, a.corners_for_avg, a.corners_against_avg):
        return None
    return (h.corners_for_avg + a.corners_against_avg) / 2 + (a.corners_for_avg + h.corners_against_avg) / 2


def cards_mean(ctx: MatchContext, global_cards_avg: float | None) -> float | None:
    home, away = ctx.home, ctx.away
    if min(_count_with(home.history[:10], "cards_for"), _count_with(away.history[:10], "cards_for")) < MIN_COUNT_MATCHES:
        return None
    h, a = home.form10, away.form10
    if None in (h.cards_for_avg, h.cards_against_avg, a.cards_for_avg, a.cards_against_avg):
        return None
    mean = 0.5 * (h.cards_for_avg + a.cards_against_avg) + 0.5 * (a.cards_for_avg + h.cards_against_avg)
    if ctx.referee_cards_avg is not None and global_cards_avg:
        mean *= min(1.25, max(0.80, ctx.referee_cards_avg / global_cards_avg))
    return mean


def _team_multipliers(team: TeamContext, cfg: ModelConfig, side: str) -> tuple[float, float, list[Adjustment]]:
    """(attack multiplier, multiplier on goals conceded, adjustments applied)."""
    adjustments: list[Adjustment] = []
    attack = 1.0
    conceded = 1.0
    if team.absences.attack_loss:
        attack *= 1 - team.absences.attack_loss
        adjustments.append(Adjustment("injuries", side, "attack", 1 - team.absences.attack_loss))
    if team.absences.defence_gain:
        conceded *= 1 + team.absences.defence_gain
        adjustments.append(Adjustment("injuries", side, "defence", 1 + team.absences.defence_gain))
    fatigue = fatigue_multiplier(team.fatigue, cfg.short_rest_days, cfg.fatigue_penalty)
    if fatigue != 1.0:
        attack *= fatigue
        conceded *= 2 - fatigue
        adjustments.append(Adjustment("fatigue", side, "attack", fatigue))
    motivation = motivation_multiplier(team.motivation, cfg.motivation_max_effect)
    if abs(motivation - 1.0) > 1e-9:
        attack *= motivation
        adjustments.append(Adjustment("motivation", side, "attack", motivation))
    return attack, conceded, adjustments


def build_match_model(ctx: MatchContext, models: FittedModels, cfg: ModelConfig) -> MatchModel:
    base_home, base_away = models.ratings.expected_goals(ctx.home.team_id, ctx.away.team_id)
    home_attack, home_conceded, home_adj = _team_multipliers(ctx.home, cfg, "home")
    away_attack, away_conceded, away_adj = _team_multipliers(ctx.away, cfg, "away")
    lambda_home = base_home * home_attack * away_conceded
    lambda_away = base_away * away_attack * home_conceded

    elo_home, elo_away = models.elo_of(ctx.home.team_id), models.elo_of(ctx.away.team_id)
    home_advantage = models.elo_settings.home_advantage
    goal_model = GoalModel(
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        rho=models.ratings.rho,
        first_half_fraction=models.first_half_fraction,
        elo_probabilities=outcome_probabilities(elo_home, elo_away, home_advantage),
        elo_weight=cfg.elo_weight,
        corners_mean=corners_mean(ctx),
        cards_mean=cards_mean(ctx, models.global_cards_avg),
        corners_dispersion=cfg.corners_dispersion,
        cards_dispersion=cfg.cards_dispersion,
    )
    return MatchModel(
        goal_model=goal_model,
        base_lambdas=(base_home, base_away),
        lambdas=(lambda_home, lambda_away),
        adjustments=tuple(home_adj + away_adj),
        elo_home=elo_home,
        elo_away=elo_away,
        elo_expected_home=expected_score(elo_home, elo_away, home_advantage),
    )


def global_cards_average(totals: Sequence[int]) -> float | None:
    return fmean(totals) if len(totals) >= 20 else None
