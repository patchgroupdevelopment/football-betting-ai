"""Hand-built analysis contexts, for testing the model and selection logic without a database."""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.analyzers.context import MatchContext, TeamContext
from backend.analyzers.data_quality import DataQuality, level_for
from backend.analyzers.fatigue import fatigue_info
from backend.analyzers.form import form_stats
from backend.analyzers.h2h import H2HSummary
from backend.analyzers.injuries import AbsenceImpact
from backend.analyzers.motivation import MotivationInfo
from backend.analyzers.team_history import TeamMatch
from backend.predictors.dixon_coles import TeamRatings
from backend.predictors.market_odds import OddsQuote
from backend.predictors.model import FittedModels

KICKOFF = datetime(2026, 10, 5, 15, 30)


def team_matches(
    team_id: int,
    scores: list[tuple[int, int]],
    *,
    xg: tuple[float, float] | None = (1.5, 1.0),
    corners: tuple[int, int] = (6, 4),
    cards: tuple[int, int] = (2, 2),
    rest_days: int = 7,
) -> list[TeamMatch]:
    """Newest first; alternates home/away. ``scores`` are (goals for, goals against)."""
    return [
        TeamMatch(
            match_id=team_id * 1000 + i,
            kickoff_utc=KICKOFF - timedelta(days=rest_days + 7 * i),
            league_id=1,
            is_home=i % 2 == 0,
            opponent_id=900 + i,
            goals_for=gf,
            goals_against=ga,
            ht_for=min(gf, 1),
            ht_against=0,
            xg_for=xg[0] if xg else None,
            xg_against=xg[1] if xg else None,
            corners_for=corners[0],
            corners_against=corners[1],
            cards_for=cards[0],
            cards_against=cards[1],
        )
        for i, (gf, ga) in enumerate(scores)
    ]


def team_context(
    team_id: int,
    name: str,
    is_home: bool,
    history: list[TeamMatch],
    *,
    absences: AbsenceImpact | None = None,
    motivation: MotivationInfo | None = None,
) -> TeamContext:
    venue = [m for m in history if m.is_home == is_home]
    return TeamContext(
        team_id=team_id,
        name=name,
        is_home=is_home,
        history=tuple(history),
        venue_history=tuple(venue),
        form5=form_stats(history[:5]),
        form10=form_stats(history[:10]),
        venue5=form_stats(venue[:5]),
        venue10=form_stats(venue[:10]),
        fatigue=fatigue_info(history, KICKOFF),
        motivation=motivation or MotivationInfo(60, ("unknown",)),
        absences=absences or AbsenceImpact(),
    )


def quality(score: int = 95) -> DataQuality:
    return DataQuality(score=score, level=level_for(score), components={"odds": True})


def match_context(
    home: TeamContext, away: TeamContext, quotes: list[OddsQuote], *, quality_score: int = 95, h2h: H2HSummary | None = None
) -> MatchContext:
    return MatchContext(
        match_id=1,
        api_id=1001,
        league_id=1,
        league_title="İngiltərə — Premyer Liqa",
        season=2026,
        round_name="Regular Season - 7",
        kickoff_utc=KICKOFF,
        kickoff_local=KICKOFF + timedelta(hours=4),
        home=home,
        away=away,
        h2h=h2h or H2HSummary(),
        quality=quality(quality_score),
        quotes=tuple(quotes),
    )


def quote(market: str, selection: str, line: float | None, price: float, bookmaker: str = "Bet365") -> OddsQuote:
    return OddsQuote(bookmaker, None, market, selection, line, price)


def goal_models(lambda_home_target: float = 1.755, lambda_away_target: float = 1.2) -> FittedModels:
    """Ratings giving roughly the requested expected goals for team 1 (home) vs team 2 (away)."""
    base, home_advantage = 1.0, 1.2
    ratings = TeamRatings(
        attack={1: lambda_home_target / (base * home_advantage * 1.17), 2: lambda_away_target},
        defence={1: 1.0, 2: 1.17},
        base=base,
        home_advantage=home_advantage,
        rho=0.0,
    )
    return FittedModels(ratings=ratings, elo={1: 1560.0, 2: 1490.0})


def value_scenario() -> MatchContext:
    """A high-scoring home side against a leaky away side, with one generous bookmaker on 1.5 OVER."""
    home = team_context(1, "Qarabağ", True, team_matches(1, [(3, 1), (2, 1), (3, 0), (2, 2), (4, 1)] * 2, xg=(1.8, 1.0)))
    away = team_context(2, "Neftçi", False, team_matches(2, [(1, 2), (0, 3), (2, 2), (1, 3), (1, 1)] * 2, xg=(1.1, 1.7)))
    quotes = [
        quote("OU", "OVER", 1.5, 1.40, "Bet365"),
        quote("OU", "UNDER", 1.5, 2.90, "Bet365"),
        quote("OU", "OVER", 1.5, 1.50, "Pinnacle"),
        quote("OU", "UNDER", 1.5, 2.55, "Pinnacle"),
    ]
    return match_context(home, away, quotes)
