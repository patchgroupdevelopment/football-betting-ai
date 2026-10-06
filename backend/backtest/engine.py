"""Walk-forward backtest: every day is analysed with only what was known before it.

For each day of the period:
1. results of all earlier matches are added to the history (nothing from the day itself);
2. the models are refitted on the live fitting window (``fit_window_days``);
3. every match of the day is evaluated by the live engine (``evaluate_match``) with the odds
   available before the round (main leagues) or at kick-off (extra leagues: closing odds only);
4. every candidate is stored with its outcome and the closing price, so different selection
   rules can be replayed later without re-running the models (``backend.backtest.simulate``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.backtest.data import HistoricalMatch, LeagueSource
from backend.backtest.history import HistoryBook, build_context
from backend.config import AppConfig
from backend.models.constants import RiskLevel
from backend.predictors.market_odds import build_market, market_draw_probability
from backend.predictors.model import fit_models
from backend.selection.engine import evaluate_match
from backend.services.settlement import FinalScore, Outcome, settle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateRecord:
    day: date
    match_index: int
    league_id: int
    kickoff_utc: datetime
    market: str
    selection: str
    line: float | None
    odds: float  # best price over the bookmakers
    bookmaker: str  # who offers the best price
    median_odds: float  # a typical bookmaker's price
    p_model: float
    p_market: float
    push_model: float
    market_draw: float | None
    confidence: int
    risk: str
    blocked: bool  # blocking flag or high risk: never a bet, whatever the value
    family: str
    outcome: Outcome | None
    closing_probability: float | None  # margin-free closing probability of this selection
    closing_price: bool  # the price is itself a closing price (no CLV)


@dataclass(frozen=True)
class MatchRecord:
    index: int
    day: date
    league_id: int
    kickoff_utc: datetime
    home: str
    away: str
    home_goals: int
    away_goals: int
    quality: int
    decision: str  # the live engine's decision for the match (Decision)
    model_1x2: tuple[float, float, float]
    market_1x2: tuple[float, float, float] | None
    closing_1x2: tuple[float, float, float] | None
    model_over25: float
    market_over25: float | None


@dataclass
class BacktestRun:
    start: date
    end: date
    leagues: list[LeagueSource]
    candidates: list[CandidateRecord] = field(default_factory=list)
    matches: list[MatchRecord] = field(default_factory=list)
    skipped_no_odds: int = 0
    history_matches: int = 0

    def match(self, index: int) -> MatchRecord:
        return self._by_index[index]

    def __post_init__(self) -> None:
        self._by_index: dict[int, MatchRecord] = {}

    def add_match(self, record: MatchRecord) -> None:
        self.matches.append(record)
        self._by_index[record.index] = record


def _day_start_utc(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _fair_1x2(market: dict) -> tuple[float, float, float] | None:
    values = [market.get(("1X2", s, None)) for s in ("1", "X", "2")]
    if any(v is None or v.fair_probability is None for v in values):
        return None
    return tuple(v.fair_probability for v in values)  # type: ignore[return-value]


def _final_score(m: HistoricalMatch) -> FinalScore:
    corners = m.home_corners + m.away_corners if m.home_corners is not None and m.away_corners is not None else None
    cards = m.home_cards + m.away_cards if m.home_cards is not None and m.away_cards is not None else None
    return FinalScore(m.home_goals, m.away_goals, m.ht_home_goals, m.ht_away_goals, corners, cards)


def run_backtest(
    matches: Sequence[HistoricalMatch],
    leagues: Sequence[LeagueSource],
    config: AppConfig,
    start: date,
    end: date,
    tz: ZoneInfo,
    *,
    progress: Callable[[date], None] | None = None,
) -> BacktestRun:
    titles = {source.league_id: source.title for source in leagues}
    run = BacktestRun(start=start, end=end, leagues=list(leagues))
    by_day: dict[date, list[HistoricalMatch]] = defaultdict(list)
    for m in matches:
        by_day[m.kickoff_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()].append(m)

    book = HistoryBook()
    pointer = 0
    ordered = sorted(matches, key=lambda m: (m.kickoff_utc, m.index))
    window = timedelta(days=config.model.fit_window_days)
    for day in sorted(by_day):
        if day > end:
            break
        day_start = _day_start_utc(day, tz)
        while pointer < len(ordered) and ordered[pointer].kickoff_utc < day_start:
            book.add(ordered[pointer])
            pointer += 1
        if day < start:
            continue
        if progress is not None:
            progress(day)

        models = fit_models(book.window(day_start - window), day_start, config.model)
        for m in by_day[day]:
            quotes = m.odds or m.closing_odds
            if not quotes:
                run.skipped_no_odds += 1
                continue
            _evaluate(run, book, m, quotes, models, config, titles.get(m.league_id, ""), tz, day)
    run.history_matches = len(book.finished)
    return run


def _evaluate(run, book, m, quotes, models, config, title, tz, day) -> None:  # noqa: ANN001 — internal helper
    ctx = build_context(book, m, quotes, title, tz)
    evaluation = evaluate_match(ctx, models, config)
    goal_model = evaluation.match_model.goal_model
    opening = build_market(quotes)
    closing = build_market(m.closing_odds) if m.closing_odds else {}
    closing_price = not m.odds
    market_draw = market_draw_probability(opening)
    score = _final_score(m)

    for evaluated in evaluation.candidates:
        c = evaluated.candidate
        probability = goal_model.probability(c.market, c.selection, c.line)
        closing_quote = closing.get(c.key)
        run.candidates.append(
            CandidateRecord(
                day=day,
                match_index=m.index,
                league_id=m.league_id,
                kickoff_utc=m.kickoff_utc,
                market=c.market,
                selection=c.selection,
                line=c.line,
                odds=c.odds,
                bookmaker=c.bookmaker,
                median_odds=opening[c.key].median_odds,
                p_model=c.p_model,
                p_market=c.p_market,
                push_model=probability.push if probability else 0.0,
                market_draw=market_draw,
                confidence=evaluated.confidence,
                risk=str(evaluated.risk),
                blocked=bool(evaluated.flags) or evaluated.risk == RiskLevel.HIGH,
                family=c.family,
                outcome=settle(c.market, c.selection, c.line, score),
                closing_probability=closing_quote.fair_probability if closing_quote else None,
                closing_price=closing_price,
            )
        )

    over = goal_model.probability("OU", "OVER", 2.5)
    over_market = opening.get(("OU", "OVER", 2.5))
    run.add_match(
        MatchRecord(
            index=m.index,
            day=day,
            league_id=m.league_id,
            kickoff_utc=m.kickoff_utc,
            home=m.home,
            away=m.away,
            home_goals=m.home_goals,
            away_goals=m.away_goals,
            quality=evaluation.context.quality.score,
            decision=str(evaluation.decision),
            model_1x2=goal_model.outcome_probabilities(),
            market_1x2=_fair_1x2(opening),
            closing_1x2=_fair_1x2(closing),
            model_over25=over.win if over else 0.0,
            market_over25=over_market.fair_probability if over_market else None,
        )
    )
