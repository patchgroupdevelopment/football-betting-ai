"""Candidates, confidence, risk and the BET / WATCH / NO_BET decision."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.analyzers.injuries import AbsenceImpact
from backend.config import AppConfig, ConfidenceWeights, SelectionConfig
from backend.models.constants import Decision, RiskLevel
from backend.predictors.market_odds import build_market
from backend.predictors.model import build_match_model
from backend.selection.candidates import Candidate, build_candidates
from backend.selection.engine import evaluate_match, select_top
from backend.selection.scoring import confidence_score, decide
from tests.builders import goal_models, match_context, quote, value_scenario

CONFIG = AppConfig()


def _evaluate(ctx, config: AppConfig = CONFIG):
    return evaluate_match(ctx, goal_models(), config)


def test_value_pick_is_a_bet():
    evaluation = _evaluate(value_scenario())
    best = evaluation.best
    assert best is not None, evaluation.match_flags
    assert (best.candidate.market, best.candidate.selection, best.candidate.line) == ("OU", "OVER", 1.5)
    assert best.candidate.odds == 1.50 and best.candidate.bookmaker == "Pinnacle"  # best price is used
    assert best.decision == Decision.BET, (best.confidence, best.candidate.ev, best.flags, best.not_bet, best.factors)
    assert best.confidence >= CONFIG.selection.min_confidence
    assert best.candidate.ev >= CONFIG.selection.min_ev
    assert best.risk in (RiskLevel.LOW, RiskLevel.MEDIUM)
    assert evaluation.why and evaluation.analysis
    assert any("ehtimalla uduzur" in line for line in evaluation.against)
    assert any("Start heyətləri hələ açıqlanmayıb" in line for line in evaluation.against)


def test_final_probability_blends_model_and_market():
    ctx = value_scenario()
    candidate = _evaluate(ctx).best.candidate
    w = CONFIG.model.market_blend_weight
    assert candidate.p_final == pytest.approx(w * candidate.p_model + (1 - w) * candidate.p_market)
    assert candidate.ev == pytest.approx(candidate.p_final * candidate.odds - 1)
    assert candidate.edge == pytest.approx(candidate.p_final - 1 / candidate.odds)


def test_large_model_market_disagreement_blocks_the_bet():
    ctx = value_scenario()
    cheap = [quote("OU", "OVER", 1.5, 1.68, "A"), quote("OU", "UNDER", 1.5, 2.15, "A")]
    best = _evaluate(replace(ctx, quotes=tuple(cheap))).best
    assert best.decision == Decision.NO_BET and "model_market_conflict" in best.flags


def test_thin_data_blocks_the_bet():
    ctx = value_scenario()
    evaluation = _evaluate(match_context(ctx.home, ctx.away, list(ctx.quotes), quality_score=50))
    assert evaluation.decision == Decision.NO_BET
    assert "low_data" in evaluation.best.flags and evaluation.best.confidence <= 60  # capped by data quality


def test_no_odds_means_no_bet():
    ctx = value_scenario()
    evaluation = _evaluate(replace(ctx, quotes=()))
    assert evaluation.best is None and evaluation.decision == Decision.NO_BET
    assert evaluation.not_bet_reasons == ("no_odds",)


def test_odds_outside_range_mean_no_candidate():
    ctx = value_scenario()
    short = [quote("OU", "OVER", 1.5, 1.12), quote("OU", "UNDER", 1.5, 6.0)]
    evaluation = _evaluate(replace(ctx, quotes=tuple(short)))
    assert evaluation.best is None and evaluation.match_flags == ("no_market_in_range",)


def test_critical_absence_blocks_goal_markets():
    ctx = value_scenario()
    critical = AbsenceImpact(attack_loss=0.15, critical=True, notes=("key_attacker_out",))
    evaluation = _evaluate(replace(ctx, home=replace(ctx.home, absences=critical)))
    assert evaluation.decision == Decision.NO_BET and "critical_absence" in evaluation.best.flags


def test_market_filter_in_config():
    config = AppConfig(selection=SelectionConfig(markets=["1X2"]))
    assert _evaluate(value_scenario(), config).best is None


def _candidate(ev: float, odds: float = 1.5) -> Candidate:
    return Candidate("OU", "OVER", 1.5, odds, "A", 0.75, 0.70, 0.72, 0.0, ev, "goals", None, "OVER")


@pytest.mark.parametrize(
    ("ev", "confidence", "expected"),
    [
        (0.05, 80, Decision.BET),
        (0.01, 80, Decision.WATCH),  # positive but below the minimum value
        (0.05, 70, Decision.WATCH),  # confidence within the watch margin
        (0.05, 60, Decision.NO_BET),
        (-0.02, 85, Decision.NO_BET),
    ],
)
def test_decision_rules(ev, confidence, expected):
    decision, _ = decide(_candidate(ev), confidence, RiskLevel.LOW, [], SelectionConfig())
    assert decision == expected


def test_blocking_flags_and_high_risk_win_over_value():
    assert decide(_candidate(0.10), 90, RiskLevel.LOW, ["critical_absence"], SelectionConfig())[0] == Decision.NO_BET
    decision, reasons = decide(_candidate(0.10), 90, RiskLevel.HIGH, [], SelectionConfig())
    assert decision == Decision.NO_BET and reasons == ["high_risk"]


def test_confidence_excludes_non_applicable_and_caps_by_data():
    weights = ConfidenceWeights()
    factors = {"form": 80.0, "home_away": 80.0, "xg": None, "injuries": None, "strength": 80.0, "h2h": None,
               "motivation": None, "fatigue": None, "market": 80.0}
    assert confidence_score(factors, weights, quality_score=95, family="corners") == 80
    assert confidence_score(dict.fromkeys(factors, 100.0), weights, quality_score=60, family="goals") == 70
    assert confidence_score(dict.fromkeys(factors, 100.0), weights, quality_score=95, family="cards") == 80


def test_draw_no_bet_ev_accounts_for_refund():
    ctx = value_scenario()
    quotes = [quote("DNB", "1", None, 1.45), quote("DNB", "2", None, 2.70),
              quote("1X2", "1", None, 1.95), quote("1X2", "X", None, 3.60), quote("1X2", "2", None, 3.90)]
    match_model = build_match_model(ctx, goal_models(), CONFIG.model)
    candidates = build_candidates(match_model, build_market(quotes), CONFIG.selection, 0.4)
    dnb = next(c for c in candidates if c.market == "DNB")
    assert dnb.push > 0.15
    assert dnb.ev == pytest.approx((1 - dnb.push) * (dnb.p_final * dnb.odds - 1))


def test_select_top_limits_and_orders():
    good = _evaluate(value_scenario())
    assert select_top([good, good, good, good], 3) == [good, good, good]
    assert select_top([_evaluate(replace(value_scenario(), quotes=()))], 3) == []
