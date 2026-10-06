"""Prices the user checked at their own bookmaker, and what they say about that bookmaker.

- gap to fair: user's price ÷ the pick's minimum (fair) price − 1; below zero means the bookmaker pays
  less than the market's fair price, i.e. roughly its margin on these picks;
- gap to best: user's price ÷ the best market price − 1 (what line shopping would have added);
- results: the picks that passed the check (✅), and all checked picks, settled at the user's price.
Only the latest check of each pick counts (prices move; the last one is what the user could take).
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.database.session import Database
from backend.models import PriceCheck, Prediction, Result
from backend.models.constants import FINISHED_STATUSES
from backend.services.picks import PickView
from backend.services.results import final_score
from backend.services.settlement import Outcome, profit, settle


def record_check(
    db: Database, view: PickView, odds: float, bookmaker: str, ev: float, min_odds: float | None, chat_id: int | None
) -> None:
    with db.session() as session:
        session.add(
            PriceCheck(
                prediction_id=view.prediction_id,
                match_id=view.match_id,
                bookmaker=bookmaker,
                odds=odds,
                min_odds=min_odds,
                best_odds=view.odds,
                p_final=view.p_final,
                ev=ev,
                chat_id=chat_id,
            )
        )


@dataclass(frozen=True)
class CheckResults:
    bets: int
    wins: int
    losses: int
    profit: float

    @property
    def roi(self) -> float | None:
        return self.profit / self.bets if self.bets else None


@dataclass(frozen=True)
class BookmakerStats:
    bookmaker: str
    checks: int
    passed: int  # price at or above the minimum (✅)
    gap_to_fair: float | None
    gap_to_best: float | None
    passed_results: CheckResults
    all_results: CheckResults

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.checks if self.checks else None


def _outcome(session: Session, check: PriceCheck) -> Outcome | None:
    match = check.prediction.match
    if match.status not in FINISHED_STATUSES or match.home_goals is None or match.away_goals is None:
        return None
    result = session.scalar(select(Result).where(Result.match_id == match.id))
    prediction = check.prediction
    return settle(prediction.market, prediction.selection, prediction.line, final_score(match, result))


def _results(rows: list[tuple[PriceCheck, Outcome | None]]) -> CheckResults:
    settled = [(c, o) for c, o in rows if o is not None]
    return CheckResults(
        bets=sum(1 for _, o in settled if o != "push"),
        wins=sum(1 for _, o in settled if o == "win"),
        losses=sum(1 for _, o in settled if o == "loss"),
        profit=round(sum(profit(o, c.odds) for c, o in settled), 2),
    )


def bookmaker_stats(db: Database, bookmaker: str) -> BookmakerStats:
    with db.session() as session:
        checks = list(
            session.scalars(
                select(PriceCheck)
                .options(joinedload(PriceCheck.prediction).joinedload(Prediction.match))
                .where(PriceCheck.bookmaker == bookmaker)
                .order_by(PriceCheck.id)
            )
        )
        latest: dict[int, PriceCheck] = {}
        for check in checks:
            latest[check.prediction_id] = check  # later checks replace earlier ones
        rows = [(c, _outcome(session, c)) for c in latest.values()]

    fair_gaps = [c.odds / c.min_odds - 1 for c, _ in rows if c.min_odds]
    best_gaps = [c.odds / c.best_odds - 1 for c, _ in rows if c.best_odds]
    passed = [(c, o) for c, o in rows if c.ev >= 0]
    return BookmakerStats(
        bookmaker=bookmaker,
        checks=len(rows),
        passed=len(passed),
        gap_to_fair=fmean(fair_gaps) if fair_gaps else None,
        gap_to_best=fmean(best_gaps) if best_gaps else None,
        passed_results=_results(passed),
        all_results=_results(rows),
    )
