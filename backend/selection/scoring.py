"""Confidence score, risk level, blocking flags and the final decision for a candidate.

Each factor (form, home/away, xG, injuries, strength, H2H, motivation, fatigue,
market) is scored 0–100 by how much it *supports this specific selection*
(50 = neutral). Factors that do not apply to a market (e.g. fatigue for a
corners bet) are ``None`` and excluded from the weighted average; factors that
apply but lack data score 50, which pulls the confidence down.

The confidence is then capped by data completeness: it can never exceed the
data-quality score by more than 10 points.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import fmean

from backend.analyzers.context import MatchContext, TeamContext
from backend.analyzers.form import (
    FormStats,
    btts_rate,
    cards_over_rate,
    corners_over_rate,
    first_half_over_rate,
    team_goals_over_rate,
    total_goals_over_rate,
)
from backend.config import AppConfig, ConfidenceWeights, SelectionConfig
from backend.models.constants import Decision, RiskLevel
from backend.predictors.model import MatchModel
from backend.selection.candidates import Candidate

Factors = dict[str, float | None]
MIN_FORM_MATCHES = 3
COUNT_MARKET_CAP = 80
DATA_CAP_MARGIN = 10
CONFLICT_THRESHOLD = 40
CONFLICT_COUNT = 3


def _tanh(x: float, scale: float) -> float:
    return 50.0 + 50.0 * math.tanh(x / scale)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _mean(*values: float | None) -> float | None:
    known = [v for v in values if v is not None]
    return fmean(known) if known else None


def _blend(recent: float | None, longer: float | None) -> float | None:
    if recent is None and longer is None:
        return None
    if recent is None or longer is None:
        return recent if recent is not None else longer
    return 0.6 * recent + 0.4 * longer


def _directional(rate_value: float | None, direction: str | None) -> float:
    """A 0–1 rate of the 'over'/'yes' event, as support for the chosen direction."""
    if rate_value is None:
        return 50.0
    return 100.0 * (rate_value if direction in ("OVER", "YES") else 1.0 - rate_value)


def _sign(direction: str | None) -> int:
    return 1 if direction in ("OVER", "YES") else -1


def _ppg_diff(a: FormStats, b: FormStats) -> float | None:
    if a.matches < MIN_FORM_MATCHES or b.matches < MIN_FORM_MATCHES:
        return None
    return a.ppg - b.ppg


def _market_agreement(c: Candidate) -> float:
    return _clamp(100 - 400 * abs(c.p_model - c.p_market))


def _absence_load(team: TeamContext) -> float:
    return team.absences.attack_loss + team.absences.defence_gain


def _rest(team: TeamContext) -> float:
    return min(team.fatigue.rest_days, 7.0) if team.fatigue.rest_days is not None else 7.0


def _expected_xg(attacking: FormStats, defending: FormStats) -> float | None:
    if attacking.xg_for_avg is None or defending.xg_against_avg is None:
        return None
    return (attacking.xg_for_avg + defending.xg_against_avg) / 2


# ------------------------------------------------------------------ families


def _side_factors(c: Candidate, ctx: MatchContext, mm: MatchModel) -> Factors:
    own, opp = (ctx.home, ctx.away) if c.side == "home" else (ctx.away, ctx.home)
    form5, form10 = _ppg_diff(own.form5, opp.form5), _ppg_diff(own.form10, opp.form10)
    venue5, venue10 = _ppg_diff(own.venue5, opp.venue5), _ppg_diff(own.venue10, opp.venue10)
    form = _blend(form5, form10)
    venue = _blend(venue5, venue10)

    xg = 50.0
    o, p = own.form10, opp.form10
    if None not in (o.xg_for_avg, o.xg_against_avg, p.xg_for_avg, p.xg_against_avg):
        xg = _tanh((o.xg_for_avg - o.xg_against_avg) - (p.xg_for_avg - p.xg_against_avg), 1.0)

    h2h = None
    if ctx.h2h.count >= 2:
        points = sum(m.points if c.side == "home" else {"W": 0, "D": 1, "L": 3}[m.outcome] for m in ctx.h2h.matches)
        h2h = 100.0 * points / (3 * ctx.h2h.count)

    strength = mm.elo_expected_home if c.side == "home" else 1 - mm.elo_expected_home
    return {
        "form": _tanh(form, 1.2) if form is not None else 50.0,
        "home_away": _tanh(venue, 1.2) if venue is not None else 50.0,
        "xg": xg,
        "injuries": _tanh(_absence_load(opp) - _absence_load(own), 0.12),
        "strength": 100.0 * strength,
        "h2h": h2h if h2h is not None else 50.0,
        "motivation": _clamp(50 + (own.motivation.level - opp.motivation.level) / 2),
        "fatigue": _clamp(50 + 8 * max(-4.0, min(4.0, _rest(own) - _rest(opp)))),
        "market": _market_agreement(c),
    }


def _total_factors(c: Candidate, ctx: MatchContext, mm: MatchModel, models_first_half: float) -> Factors:
    first_half = c.family == "first_half"
    line = c.line or 0.0
    over = first_half_over_rate if first_half else total_goals_over_rate
    home, away = ctx.home, ctx.away
    form = _blend(
        _mean(over(home.history[:5], line), over(away.history[:5], line)),
        _mean(over(home.history[:10], line), over(away.history[:10], line)),
    )
    venue = _mean(over(home.venue_history[:10], line), over(away.venue_history[:10], line))
    scale = models_first_half if first_half else 1.0

    xg = 50.0
    expected_home, expected_away = _expected_xg(home.form10, away.form10), _expected_xg(away.form10, home.form10)
    if expected_home is not None and expected_away is not None:
        xg = _tanh(_sign(c.direction) * ((expected_home + expected_away) * scale - line), 0.9)

    absences = (home.absences.defence_gain + away.absences.defence_gain) - (
        home.absences.attack_loss + away.absences.attack_loss
    )
    h2h = None
    if ctx.h2h.count >= 2:
        h2h = over(list(ctx.h2h.matches), line)

    levels = (home.motivation.level, away.motivation.level)
    motivation = 50.0
    if min(levels) >= 85:
        motivation = 45.0 if _sign(c.direction) > 0 else 55.0
    elif min(levels) <= 45:
        motivation = 55.0 if _sign(c.direction) > 0 else 45.0

    return {
        "form": _directional(form, c.direction),
        "home_away": _directional(venue, c.direction),
        "xg": xg,
        "injuries": _tanh(_sign(c.direction) * absences, 0.10),
        "strength": _tanh(_sign(c.direction) * (mm.total_goals * scale - line), 0.9),
        "h2h": _directional(h2h, c.direction),
        "motivation": motivation,
        "fatigue": None,
        "market": _market_agreement(c),
    }


def _btts_factors(c: Candidate, ctx: MatchContext, mm: MatchModel) -> Factors:
    home, away = ctx.home, ctx.away
    form = _blend(
        _mean(btts_rate(home.history[:5]), btts_rate(away.history[:5])),
        _mean(btts_rate(home.history[:10]), btts_rate(away.history[:10])),
    )
    venue = _mean(btts_rate(home.venue_history[:10]), btts_rate(away.venue_history[:10]))
    xg = 50.0
    expected_home, expected_away = _expected_xg(home.form10, away.form10), _expected_xg(away.form10, home.form10)
    if expected_home is not None and expected_away is not None:
        xg = _directional((1 - math.exp(-expected_home)) * (1 - math.exp(-expected_away)), c.direction)
    absences = (home.absences.defence_gain + away.absences.defence_gain) - (
        home.absences.attack_loss + away.absences.attack_loss
    )
    lambda_home, lambda_away = mm.lambdas
    model_btts = (1 - math.exp(-lambda_home)) * (1 - math.exp(-lambda_away))
    h2h = btts_rate(list(ctx.h2h.matches)) if ctx.h2h.count >= 2 else None
    return {
        "form": _directional(form, c.direction),
        "home_away": _directional(venue, c.direction),
        "xg": xg,
        "injuries": _tanh(_sign(c.direction) * absences, 0.10),
        "strength": _directional(model_btts, c.direction),
        "h2h": _directional(h2h, c.direction),
        "motivation": 50.0,
        "fatigue": None,
        "market": _market_agreement(c),
    }


def _team_goal_factors(c: Candidate, ctx: MatchContext, mm: MatchModel) -> Factors:
    team, opp = (ctx.home, ctx.away) if c.side == "home" else (ctx.away, ctx.home)
    line = c.line or 0.0
    form = _blend(
        _mean(team_goals_over_rate(team.history[:5], line), team_goals_over_rate(opp.history[:5], line, conceded=True)),
        _mean(team_goals_over_rate(team.history[:10], line), team_goals_over_rate(opp.history[:10], line, conceded=True)),
    )
    venue = _mean(
        team_goals_over_rate(team.venue_history[:10], line),
        team_goals_over_rate(opp.venue_history[:10], line, conceded=True),
    )
    expected = _expected_xg(team.form10, opp.form10)
    team_lambda = mm.lambdas[0] if c.side == "home" else mm.lambdas[1]
    h2h = None
    if ctx.h2h.count >= 2:
        h2h = sum(1 for m in ctx.h2h.matches if (m.goals_for if c.side == "home" else m.goals_against) > line) / ctx.h2h.count
    sign = _sign(c.direction)
    return {
        "form": _directional(form, c.direction),
        "home_away": _directional(venue, c.direction),
        "xg": _tanh(sign * (expected - line), 0.8) if expected is not None else 50.0,
        "injuries": _tanh(sign * (opp.absences.defence_gain - team.absences.attack_loss), 0.08),
        "strength": _tanh(sign * (team_lambda - line), 0.8),
        "h2h": _directional(h2h, c.direction),
        "motivation": _clamp(50 + sign * (team.motivation.level - opp.motivation.level) / 4),
        "fatigue": _clamp(50 + sign * 4 * max(-4.0, min(4.0, _rest(team) - _rest(opp)))),
        "market": _market_agreement(c),
    }


def _count_factors(c: Candidate, ctx: MatchContext, mean: float | None, corners: bool) -> Factors:
    line = c.line or 0.0
    over = corners_over_rate if corners else cards_over_rate
    form = _mean(over(ctx.home.history[:10], line), over(ctx.away.history[:10], line))
    venue = _mean(over(ctx.home.venue_history[:10], line), over(ctx.away.venue_history[:10], line))
    strength = _tanh(_sign(c.direction) * (mean - line), 2.0 if corners else 1.2) if mean is not None else 50.0
    return {
        "form": _directional(form, c.direction),
        "home_away": _directional(venue, c.direction),
        "xg": None,
        "injuries": None,
        "strength": strength,
        "h2h": None,
        "motivation": None,
        "fatigue": None,
        "market": _market_agreement(c),
    }


def factor_scores(c: Candidate, ctx: MatchContext, mm: MatchModel) -> Factors:
    if c.family == "side":
        return _side_factors(c, ctx, mm)
    if c.family in ("goals", "first_half"):
        return _total_factors(c, ctx, mm, mm.goal_model.first_half_fraction)
    if c.family == "btts":
        return _btts_factors(c, ctx, mm)
    if c.family == "team_goals":
        return _team_goal_factors(c, ctx, mm)
    if c.family == "corners":
        return _count_factors(c, ctx, mm.goal_model.corners_mean, corners=True)
    return _count_factors(c, ctx, mm.goal_model.cards_mean, corners=False)


# --------------------------------------------------------- confidence & risk


def confidence_score(factors: Factors, weights: ConfidenceWeights, quality_score: int, family: str) -> int:
    weight_map = weights.model_dump()
    applicable = {name: value for name, value in factors.items() if value is not None}
    total_weight = sum(weight_map[name] for name in applicable)
    if total_weight <= 0:
        return 0
    raw = sum(weight_map[name] * value for name, value in applicable.items()) / total_weight
    cap = quality_score + DATA_CAP_MARGIN
    if family in ("corners", "cards"):
        cap = min(cap, COUNT_MARKET_CAP)
    return int(round(min(raw, cap)))


def blocking_flags(c: Candidate, factors: Factors, ctx: MatchContext, config: AppConfig) -> list[str]:
    """Conditions under which the prompt requires MƏRC YOXDUR regardless of value."""
    flags: list[str] = []
    if ctx.quality.level == "low":
        flags.append("low_data")
    if abs(c.p_model - c.p_market) * 100 > config.model.max_contradiction_pp:
        flags.append("model_market_conflict")
    weak = [name for name, value in factors.items() if name != "market" and value is not None and value < CONFLICT_THRESHOLD]
    if len(weak) >= CONFLICT_COUNT:
        flags.append("conflicting_signals")
    if c.family == "side":
        own = ctx.home if c.side == "home" else ctx.away
        if own.absences.critical:
            flags.append("critical_absence")
    elif ctx.home.absences.critical or ctx.away.absences.critical:
        flags.append("critical_absence")
    return flags


def risk_level(c: Candidate, confidence: int, ctx: MatchContext) -> RiskLevel:
    points = 0
    if c.odds > 1.55:
        points += 1
    if ctx.quality.level == "partial":
        points += 1
    elif ctx.quality.level == "low":
        points += 2
    if confidence < 65:
        points += 2
    elif confidence < 75:
        points += 1
    if c.family in ("corners", "cards", "first_half"):
        points += 1
    if abs(c.p_model - c.p_market) > 0.08:
        points += 1
    if ctx.home.absences.critical or ctx.away.absences.critical:
        points += 1
    if points <= 1:
        return RiskLevel.LOW
    return RiskLevel.MEDIUM if points <= 3 else RiskLevel.HIGH


def decide(
    c: Candidate, confidence: int, risk: RiskLevel, flags: Sequence[str], cfg: SelectionConfig
) -> tuple[Decision, list[str]]:
    """Decision plus the reasons it is not a bet (empty for BET)."""
    blockers = list(flags)
    if risk == RiskLevel.HIGH:
        blockers.append("high_risk")
    if blockers:
        return Decision.NO_BET, blockers
    if c.ev >= cfg.min_ev and confidence >= cfg.min_confidence:
        return Decision.BET, []
    misses = []
    if c.ev < cfg.min_ev:
        misses.append("low_value")
    if confidence < cfg.min_confidence:
        misses.append("low_confidence")
    if c.ev > 0 and confidence >= cfg.min_confidence - cfg.watch_margin:
        return Decision.WATCH, misses
    return Decision.NO_BET, misses
