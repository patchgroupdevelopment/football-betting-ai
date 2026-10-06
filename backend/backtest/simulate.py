"""Replays the selection rules on stored candidates, and the bankroll pyramid on the picks.

The rules mirror the live engine (scoring.decide + engine.select_top): a candidate is a bet when
nothing blocks it, EV ≥ min_ev and confidence ≥ min_confidence; one bet per match (the best by
EV × confidence); the day's best ``max_picks`` bets. Only EV depends on the model/market weight,
so the weight can be changed without re-running the models.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from backend.backtest.engine import CandidateRecord
from backend.config import BankrollConfig
from backend.services.pyramid import milestone_lock, next_balance
from backend.services.settlement import profit

Price = Literal["best", "median"]
MATCH_DURATION = timedelta(hours=2)  # a stage settles about two hours after kick-off


@dataclass(frozen=True)
class Params:
    model_weight: float
    min_ev: float
    min_confidence: int
    max_picks: int = 3
    min_odds: float = 1.20
    max_odds: float = 1.70
    price: Price = "best"


@dataclass(frozen=True)
class SimBet:
    record: CandidateRecord
    rank: int  # 1 = the day's best pick
    odds: float
    p_final: float
    ev: float
    profit: float
    clv: float | None

    @property
    def score(self) -> float:
        return self.ev * self.record.confidence / 100


def value(record: CandidateRecord, model_weight: float, price: Price = "best") -> tuple[float, float, float]:
    """(odds, final probability, EV) — the same formulas as selection.candidates.build_candidates."""
    odds = record.odds if price == "best" else record.median_odds
    p_final = model_weight * record.p_model + (1 - model_weight) * record.p_market
    push = 0.0
    if record.push_model:
        push = (
            record.push_model
            if record.market_draw is None
            else model_weight * record.push_model + (1 - model_weight) * record.market_draw
        )
    return odds, p_final, (1 - push) * (p_final * odds - 1)


def clv(record: CandidateRecord, odds: float) -> float | None:
    """Expected return of the bet at the closing (margin-free) price: > 0 means the price beat the close."""
    if record.closing_price or record.closing_probability is None:
        return None
    return odds * record.closing_probability - 1


def select_bets(records: Iterable[CandidateRecord], params: Params) -> list[SimBet]:
    by_match: dict[tuple[date, int], list[SimBet]] = defaultdict(list)
    for r in records:
        if r.blocked or r.outcome is None or r.confidence < params.min_confidence:
            continue
        odds, p_final, ev = value(r, params.model_weight, params.price)
        if ev < params.min_ev or not params.min_odds <= odds <= params.max_odds:
            continue
        by_match[(r.day, r.match_index)].append(SimBet(r, 0, odds, p_final, ev, profit(r.outcome, odds), clv(r, odds)))

    by_day: dict[date, list[SimBet]] = defaultdict(list)
    for (day, _), bets in by_match.items():
        by_day[day].append(max(bets, key=lambda b: (b.score, b.record.confidence)))

    selected: list[SimBet] = []
    for day in sorted(by_day):
        ranked = sorted(by_day[day], key=lambda b: (b.score, b.record.confidence), reverse=True)[: params.max_picks]
        selected.extend(
            SimBet(b.record, rank, b.odds, b.p_final, b.ev, b.profit, b.clv) for rank, b in enumerate(ranked, start=1)
        )
    return selected


# ------------------------------------------------------------------ pyramid


@dataclass(frozen=True)
class Stage:
    bet: SimBet
    balance_before: float
    balance_after: float  # 0 when lost


@dataclass
class Attempt:
    number: int
    stages: list[Stage] = field(default_factory=list)
    status: Literal["running", "lost", "target"] = "running"
    locked: float = 0.0
    locks: int = 0  # milestones locked so far in this attempt

    @property
    def wins(self) -> int:
        return sum(1 for s in self.stages if s.balance_after > s.balance_before)

    @property
    def peak(self) -> float:
        return max([s.balance_after for s in self.stages] + [s.balance_before for s in self.stages] or [0.0])

    @property
    def breaking_stage(self) -> Stage | None:
        return self.stages[-1] if self.status == "lost" and self.stages else None


Strategy = Literal["daily", "chain"]


def _stage_bets(bets: Sequence[SimBet], strategy: Strategy) -> list[SimBet]:
    """The bets that become pyramid stages, in the order they are played."""
    by_day: dict[date, list[SimBet]] = defaultdict(list)
    for bet in bets:
        by_day[bet.record.day].append(bet)
    stages: list[SimBet] = []
    for day in sorted(by_day):
        day_bets = by_day[day]
        if strategy == "daily":
            stages.append(min(day_bets, key=lambda b: b.rank))
            continue
        free_at = None  # the next stage can start once the previous one is settled
        for bet in sorted(day_bets, key=lambda b: (b.record.kickoff_utc, b.rank)):
            if free_at is None or bet.record.kickoff_utc >= free_at:
                stages.append(bet)
                free_at = bet.record.kickoff_utc + MATCH_DURATION
    return stages


def simulate_pyramid(bets: Sequence[SimBet], config: BankrollConfig, strategy: Strategy = "daily") -> list[Attempt]:
    attempts = [Attempt(1)]
    balance = config.starting
    for bet in _stage_bets(bets, strategy):
        attempt = attempts[-1]
        outcome = bet.record.outcome
        if outcome == "push":
            attempt.stages.append(Stage(bet, balance, balance))
            continue
        if outcome == "loss":
            attempt.stages.append(Stage(bet, balance, 0.0))
            attempt.status = "lost"
            attempts.append(Attempt(attempt.number + 1))
            balance = config.starting
            continue
        gross = next_balance(balance, bet.odds)
        locked = milestone_lock(balance, gross, config, attempt.locks)
        if locked:
            attempt.locks += 1
        attempt.locked += locked
        attempt.stages.append(Stage(bet, balance, round(gross - locked, 2)))
        balance = round(gross - locked, 2)
        if balance >= config.target:
            attempt.status = "target"
            attempts.append(Attempt(attempt.number + 1))
            balance = config.starting
    if not attempts[-1].stages and len(attempts) > 1:
        attempts.pop()  # the restart after the last stage never played
    return attempts
