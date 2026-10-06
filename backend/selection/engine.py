"""Evaluates one match end to end and picks the day's TOP selections."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.analyzers.context import MatchContext
from backend.config import AppConfig
from backend.models.constants import Decision, RiskLevel
from backend.predictors.market_odds import build_market
from backend.predictors.model import FittedModels, MatchModel, build_match_model
from backend.selection import narrative
from backend.selection.candidates import Candidate, build_candidates
from backend.selection.scoring import Factors, blocking_flags, confidence_score, decide, factor_scores, risk_level

DECISION_ORDER = {Decision.BET: 2, Decision.WATCH: 1, Decision.NO_BET: 0}


@dataclass(frozen=True)
class EvaluatedCandidate:
    candidate: Candidate
    factors: Factors
    confidence: int
    risk: RiskLevel
    flags: tuple[str, ...]
    decision: Decision
    not_bet: tuple[str, ...]

    @property
    def score(self) -> float:
        """Ranking score: value weighted by confidence."""
        return self.candidate.ev * self.confidence / 100


@dataclass(frozen=True)
class MatchEvaluation:
    context: MatchContext
    match_model: MatchModel
    candidates: tuple[EvaluatedCandidate, ...]  # best first
    match_flags: tuple[str, ...]
    why: tuple[str, ...]
    against: tuple[str, ...]
    analysis: tuple[str, ...]

    @property
    def best(self) -> EvaluatedCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def decision(self) -> Decision:
        return self.best.decision if self.best else Decision.NO_BET

    @property
    def not_bet_reasons(self) -> tuple[str, ...]:
        if self.best is None:
            return self.match_flags or ("no_market_in_range",)
        return self.best.not_bet


def evaluate_match(ctx: MatchContext, models: FittedModels, config: AppConfig) -> MatchEvaluation:
    match_model = build_match_model(ctx, models, config.model)
    market = build_market(ctx.quotes)
    candidates = build_candidates(match_model, market, config.selection, config.model.market_blend_weight)

    match_flags: list[str] = []
    if not ctx.quotes:
        match_flags.append("no_odds")
    elif not candidates:
        match_flags.append("no_market_in_range")
    if ctx.quality.level == "low":
        match_flags.append("low_data")

    evaluated: list[EvaluatedCandidate] = []
    for candidate in candidates:
        factors = factor_scores(candidate, ctx, match_model)
        confidence = confidence_score(factors, config.confidence_weights, ctx.quality.score, candidate.family)
        risk = risk_level(candidate, confidence, ctx)
        flags = blocking_flags(candidate, factors, ctx, config)
        decision, not_bet = decide(candidate, confidence, risk, flags, config.selection)
        evaluated.append(
            EvaluatedCandidate(candidate, factors, confidence, risk, tuple(flags), decision, tuple(not_bet))
        )
    evaluated.sort(key=lambda e: (DECISION_ORDER[e.decision], e.score, e.confidence), reverse=True)

    best = evaluated[0] if evaluated else None
    return MatchEvaluation(
        context=ctx,
        match_model=match_model,
        candidates=tuple(evaluated),
        match_flags=tuple(match_flags),
        why=tuple(narrative.why(best.candidate, best.factors, ctx, match_model)) if best else (),
        against=tuple(narrative.against(best.candidate, best.factors, ctx, match_model)) if best else (),
        analysis=tuple(narrative.analysis_lines(ctx, match_model)),
    )


def select_top(evaluations: Sequence[MatchEvaluation], max_picks: int) -> list[MatchEvaluation]:
    """One pick per match (correlated bets on the same game are avoided), best value first."""
    bets = [e for e in evaluations if e.decision == Decision.BET and e.best is not None]
    return sorted(bets, key=lambda e: (e.best.score, e.best.confidence), reverse=True)[:max_picks]


def select_watch(evaluations: Sequence[MatchEvaluation], count: int = 3) -> list[MatchEvaluation]:
    watch = [e for e in evaluations if e.decision == Decision.WATCH and e.best is not None]
    return sorted(watch, key=lambda e: (e.best.score, e.best.confidence), reverse=True)[:count]
