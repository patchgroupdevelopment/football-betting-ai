"""Statistical models: distributions, Dixon-Coles, Elo, market probabilities, odds consensus."""

from __future__ import annotations

import math
import random

import pytest

from backend.predictors.dixon_coles import MatchObservation, fit_ratings
from backend.predictors.elo import compute_ratings, expected_score, goal_difference_multiplier, outcome_probabilities
from backend.predictors.market_odds import OddsQuote, build_market, remove_margin
from backend.predictors.markets import GoalModel, is_half_line
from backend.predictors.poisson import count_over_probability, negbin_pmf, poisson_pmf, score_matrix


def test_distributions_sum_to_one():
    assert sum(poisson_pmf(k, 2.7) for k in range(40)) == pytest.approx(1.0)
    probs = [negbin_pmf(k, 9.5, 1.4) for k in range(80)]
    mean = sum(k * p for k, p in enumerate(probs))
    variance = sum((k - mean) ** 2 * p for k, p in enumerate(probs))
    assert sum(probs) == pytest.approx(1.0) and mean == pytest.approx(9.5, rel=1e-3)
    assert variance == pytest.approx(9.5 * 1.4, rel=1e-2)  # over-dispersed by the configured factor


def test_count_over_probability_matches_poisson():
    expected = 1 - sum(poisson_pmf(k, 2.0) for k in range(3))
    assert count_over_probability(2.0, 2.5) == pytest.approx(expected)


def test_score_matrix():
    matrix = score_matrix(1.6, 1.1, rho=-0.1)
    assert sum(map(sum, matrix)) == pytest.approx(1.0)
    independent = score_matrix(1.6, 1.1, rho=0.0)
    assert matrix[0][0] > independent[0][0] and matrix[1][1] > independent[1][1]  # rho < 0 lifts 0-0 and 1-1


def _season(strengths: dict[int, tuple[float, float]], rounds: int, seed: int = 7) -> list[MatchObservation]:
    rng = random.Random(seed)

    def sample(mean: float) -> int:
        limit, k, p = math.exp(-mean), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1

    observations = []
    teams = list(strengths)
    for r in range(rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                lh = 1.1 * 1.25 * strengths[home][0] * strengths[away][1]
                la = 1.1 * strengths[away][0] * strengths[home][1]
                observations.append(MatchObservation(home, away, sample(lh), sample(la), None, None, age_days=r * 30))
    return observations


def test_dixon_coles_recovers_team_order():
    truth = {1: (1.5, 0.7), 2: (1.2, 0.9), 3: (1.0, 1.0), 4: (0.8, 1.2), 5: (0.6, 1.4)}
    ratings = fit_ratings(_season(truth, rounds=25), half_life_days=10_000, prior_matches=1)
    attack_order = sorted(truth, key=lambda t: ratings.attack[t], reverse=True)
    defence_order = sorted(truth, key=lambda t: ratings.defence[t])
    assert attack_order == [1, 2, 3, 4, 5]
    assert defence_order[0] == 1 and defence_order[-1] == 5  # sampling noise may swap close middle teams
    assert 1.05 < ratings.home_advantage < 1.5
    home, away = ratings.expected_goals(1, 5)
    assert home > 2.0 and away < 0.8


def test_prior_shrinks_sparse_teams():
    single = [MatchObservation(1, 2, 6, 0, None, None, age_days=1)]
    ratings = fit_ratings(single, prior_matches=3)
    assert 1.0 < ratings.attack[1] < 2.5  # one 6-0 win does not create a super team


def test_empty_fit_is_neutral():
    ratings = fit_ratings([])
    assert ratings.expected_goals(1, 2) == pytest.approx((ratings.base * ratings.home_advantage, ratings.base))


def test_elo_updates():
    ratings = compute_ratings([(1, 2, 3, 0), (2, 3, 1, 1)])
    assert ratings[1] > 1500 > ratings[2]
    assert sum(ratings.values()) == pytest.approx(1500 * 3)
    assert goal_difference_multiplier(1) == 1 and goal_difference_multiplier(3) == pytest.approx(14 / 8)
    assert expected_score(1500, 1500, 65) > 0.5


def test_elo_outcomes():
    even = outcome_probabilities(1500, 1500, 0)
    mismatch = outcome_probabilities(1800, 1400, 65)
    assert sum(even) == pytest.approx(1.0) and even[0] == pytest.approx(even[2])
    assert mismatch[0] > 0.7 and mismatch[1] < even[1]


def test_goal_model_market_probabilities():
    model = GoalModel(1.7, 1.1, rho=-0.05, corners_mean=10.0, cards_mean=4.2)
    home, draw, away = model.outcome_probabilities()
    assert home + draw + away == pytest.approx(1.0)
    assert model.probability("DC", "1X", None).win == pytest.approx(home + draw)
    dnb = model.probability("DNB", "1", None)
    assert dnb.push == pytest.approx(draw) and dnb.effective == pytest.approx(home / (home + away))
    over, under = model.probability("OU", "OVER", 2.5), model.probability("OU", "UNDER", 2.5)
    assert over.win + under.win == pytest.approx(1.0)
    assert model.probability("AH", "1", -0.5).win == pytest.approx(home)  # no Elo blend here
    assert model.probability("AH", "2", 0.5).win == pytest.approx(draw + away)
    assert model.probability("TEAM_TOTAL_HOME", "OVER", 0.5).win == pytest.approx(1 - math.exp(-1.7), abs=0.02)
    assert 0 < model.probability("CORNERS_OU", "OVER", 9.5).win < 1
    assert model.probability("OU", "OVER", 2.0) is None  # integer lines are not modelled
    assert model.probability("OU_1H", "OVER", 0.5).win < over.win + 0.5


def test_dnb_expected_value_refunds_draws():
    probability = GoalModel(1.4, 1.4).probability("DNB", "1", None)
    assert probability.expected_value(2.0) == pytest.approx(probability.win - (1 - probability.win - probability.push))


def test_elo_blend_moves_outcome_probabilities():
    plain = GoalModel(1.4, 1.4).outcome_probabilities()
    blended = GoalModel(1.4, 1.4, elo_probabilities=(0.7, 0.2, 0.1), elo_weight=0.5).outcome_probabilities()
    assert blended[0] > plain[0]


def test_half_line_detection():
    assert is_half_line(2.5) and is_half_line(-1.5) and not is_half_line(2.0) and not is_half_line(2.25)


def _q(book: str, market: str, selection: str, line, price: float) -> OddsQuote:
    return OddsQuote(book, None, market, selection, line, price)


def test_market_consensus_removes_margin_and_finds_best_price():
    market = build_market(
        [
            _q("A", "1X2", "1", None, 2.00), _q("A", "1X2", "X", None, 3.40), _q("A", "1X2", "2", None, 3.80),
            _q("B", "1X2", "1", None, 2.10), _q("B", "1X2", "X", None, 3.30), _q("B", "1X2", "2", None, 3.60),
            _q("A", "OU", "OVER", 2.5, 1.90), _q("A", "OU", "UNDER", 2.5, 1.95),
            _q("C", "OU", "OVER", 2.5, 2.05),  # incomplete pair: best price only
        ]
    )
    home = market[("1X2", "1", None)]
    assert home.best_odds == 2.10 and home.best_bookmaker == "B" and home.bookmakers == 2
    total = sum(market[("1X2", s, None)].fair_probability for s in ("1", "X", "2"))
    assert total == pytest.approx(1.0)
    over = market[("OU", "OVER", 2.5)]
    assert over.best_odds == 2.05 and over.fair_probability == pytest.approx(remove_margin([1.90, 1.95])[0])
    assert over.median_odds == pytest.approx(1.975)
    assert ("DC", "1X", None) not in market  # double chance appears only when a bookmaker quotes it


def test_power_margin_removal_leaves_more_probability_to_favourites():
    prices = [1.30, 5.50, 9.00]
    power = remove_margin(prices)
    inverse = [1 / p for p in prices]
    proportional = [x / sum(inverse) for x in inverse]
    assert sum(power) == pytest.approx(1.0)
    assert power[0] > proportional[0] and power[2] < proportional[2]  # favourite–longshot bias
    assert remove_margin([2.0, 2.0]) == pytest.approx([0.5, 0.5])


def test_double_chance_and_handicap_consensus():
    market = build_market(
        [
            _q("A", "1X2", "1", None, 1.80), _q("A", "1X2", "X", None, 3.60), _q("A", "1X2", "2", None, 4.50),
            _q("A", "DC", "1X", None, 1.25),
            _q("A", "AH", "1", -1.5, 2.60), _q("A", "AH", "2", 1.5, 1.50),
        ]
    )
    one, draw = market[("1X2", "1", None)].fair_probability, market[("1X2", "X", None)].fair_probability
    assert market[("DC", "1X", None)].fair_probability == pytest.approx(one + draw)
    ah_home, ah_away = market[("AH", "1", -1.5)].fair_probability, market[("AH", "2", 1.5)].fair_probability
    assert ah_home + ah_away == pytest.approx(1.0)
