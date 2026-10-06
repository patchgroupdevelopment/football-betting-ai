"""Bet candidates: model vs market for every quoted selection within the odds range.

Probabilities:
- model    — from the goal model (Dixon-Coles, Elo-blended for 1X2-type markets)
- market   — margin-free bookmaker consensus
- final    — w × model + (1 − w) × market; the market acts as a check on the model
Value:
- edge     — final probability − implied probability of the best price (1 / odds)
- EV       — expected profit per unit stake at the best price (pushes refunded)
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import SelectionConfig
from backend.predictors.market_odds import Key, MarketPrice, market_draw_probability
from backend.predictors.model import MatchModel

# Selections that are never offered: a draw is never priced 1.20–1.70, and "no draw"
# (12) has no clear team-strength story to explain or audit.
EXCLUDED = {("1X2", "X"), ("DC", "12")}


@dataclass(frozen=True)
class Candidate:
    market: str
    selection: str
    line: float | None
    odds: float
    bookmaker: str
    p_model: float  # effective win probability (conditional on no push)
    p_market: float
    p_final: float
    push: float  # probability of a refund (draw-no-bet)
    ev: float
    family: str  # side | goals | first_half | btts | team_goals | corners | cards
    side: str | None  # home | away (side and team markets)
    direction: str | None  # OVER | UNDER | YES | NO

    @property
    def implied(self) -> float:
        return 1.0 / self.odds

    @property
    def edge(self) -> float:
        return self.p_final - self.implied

    @property
    def key(self) -> Key:
        return (self.market, self.selection, self.line)


def traits(market: str, selection: str) -> tuple[str, str | None, str | None]:
    """(family, side, direction) of a selection."""
    if market in ("1X2", "DNB", "AH"):
        return "side", "home" if selection == "1" else "away", None
    if market == "DC":
        return "side", "home" if selection == "1X" else "away", None
    if market == "OU":
        return "goals", None, selection
    if market == "OU_1H":
        return "first_half", None, selection
    if market == "BTTS":
        return "btts", None, selection
    if market in ("TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY"):
        return "team_goals", "home" if market == "TEAM_TOTAL_HOME" else "away", selection
    if market == "CORNERS_OU":
        return "corners", None, selection
    return "cards", None, selection


def build_candidates(
    match_model: MatchModel, market: dict[Key, MarketPrice], selection_cfg: SelectionConfig, model_weight: float
) -> list[Candidate]:
    candidates: list[Candidate] = []
    allowed = set(selection_cfg.markets)
    market_draw = market_draw_probability(market)
    for key, price in market.items():
        name, selection, line = key
        if name not in allowed or (name, selection) in EXCLUDED or price.fair_probability is None:
            continue
        if not selection_cfg.min_odds <= price.best_odds <= selection_cfg.max_odds:
            continue
        model = match_model.goal_model.probability(name, selection, line)
        if model is None:
            continue

        p_final = model_weight * model.effective + (1 - model_weight) * price.fair_probability
        push = 0.0
        if model.push:
            push = model.push if market_draw is None else model_weight * model.push + (1 - model_weight) * market_draw
        ev = (1 - push) * (p_final * price.best_odds - 1)
        family, side, direction = traits(name, selection)
        candidates.append(
            Candidate(
                market=name,
                selection=selection,
                line=line,
                odds=price.best_odds,
                bookmaker=price.best_bookmaker,
                p_model=model.effective,
                p_market=price.fair_probability,
                p_final=p_final,
                push=push,
                ev=ev,
                family=family,
                side=side,
                direction=direction,
            )
        )
    return candidates
