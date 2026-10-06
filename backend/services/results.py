"""Settles the system's picks once their matches are over, and keeps the paper pyramid moving.

- Every paper bet whose match has finished is settled from the final score (``settlement.settle``).
  The closing price is the bookmakers' last quote stored before kick-off; CLV compares the price
  taken with that closing price after margin removal.
- Matches that were cancelled, or finished without the data a market needs (e.g. no corner count)
  for ``VOID_AFTER`` are voided: the stake is returned.
- In paper mode the pyramid follows the system: the day's top pick opens a stage when none is
  pending, and the stage is settled with its bet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.database.session import Database
from backend.models import Bet, Match, Prediction, PyramidStage, Result
from backend.models.constants import CANCELLED_STATUSES, FINISHED_STATUSES, BetStatus, StageStatus
from backend.predictors.market_odds import build_market, latest_quotes
from backend.services.pyramid import PyramidService, PyramidState
from backend.services.settlement import FinalScore, Outcome, profit, settle
from backend.utils.timeutils import utcnow

VOID_AFTER = timedelta(days=3)
_STATUS = {"win": BetStatus.WON, "loss": BetStatus.LOST, "push": BetStatus.VOID}
_STAGE = {BetStatus.WON: StageStatus.WON, BetStatus.LOST: StageStatus.LOST, BetStatus.VOID: StageStatus.VOID}


@dataclass(frozen=True)
class SettledBet:
    bet_id: int
    run_date: date | None
    rank: int | None
    home: str
    away: str
    score: str | None
    market: str
    selection: str
    line: float | None
    odds: float
    status: str  # BetStatus
    pnl: float
    clv: float | None


@dataclass
class SettlementReport:
    settled: list[SettledBet] = field(default_factory=list)
    stage_settled: StageStatus | None = None
    stage_opened: bool = False
    pyramid: PyramidState | None = None


def final_score(match: Match, result: Result | None) -> FinalScore:
    return FinalScore(
        home=match.home_goals or 0,
        away=match.away_goals or 0,
        ht_home=match.ht_home_goals,
        ht_away=match.ht_away_goals,
        corners=result.total_corners if result else None,
        cards=result.total_cards if result else None,
    )


def _closing(session: Session, prediction: Prediction, odds_taken: float) -> tuple[float | None, float | None]:
    """(closing best price, CLV) from the last quotes stored before kick-off."""
    match = prediction.match
    market = build_market(latest_quotes(session, match.id, before=match.kickoff_utc))
    price = market.get((prediction.market, prediction.selection, prediction.line))
    if price is None:
        return None, None
    clv = odds_taken * price.fair_probability - 1 if price.fair_probability else None
    return price.best_odds, clv


class ResultsService:
    def __init__(self, db: Database, pyramid: PyramidService, *, paper_mode: bool) -> None:
        self._db = db
        self._pyramid = pyramid
        self._paper_mode = paper_mode

    def settle(self, now: datetime | None = None) -> SettlementReport:
        now = now or utcnow()
        report = SettlementReport()
        with self._db.session() as session:
            rows = session.execute(
                select(Bet, Prediction)
                .join(Prediction, Prediction.id == Bet.prediction_id)
                .options(joinedload(Prediction.match).joinedload(Match.home_team), joinedload(Prediction.match).joinedload(Match.away_team))
                .where(Bet.status == BetStatus.PENDING)
                .order_by(Bet.id)
            ).all()
            for bet, prediction in rows:
                settled = self._settle_one(session, bet, prediction, now)
                if settled is not None:
                    report.settled.append(settled)
        if self._paper_mode:
            report.stage_settled = self._settle_stage()
            report.pyramid = self._pyramid.get_state()
        return report

    def _settle_one(self, session: Session, bet: Bet, prediction: Prediction, now: datetime) -> SettledBet | None:
        match = prediction.match
        outcome: Outcome | None = None
        if match.status in FINISHED_STATUSES and match.home_goals is not None and match.away_goals is not None:
            result = session.scalar(select(Result).where(Result.match_id == match.id))
            outcome = settle(prediction.market, prediction.selection, prediction.line, final_score(match, result))
        if outcome is None:
            stale = now - match.kickoff_utc > VOID_AFTER
            if not (match.status in CANCELLED_STATUSES or (stale and match.status in FINISHED_STATUSES)):
                return None
            outcome = "push"  # cancelled, or the market cannot be decided from the data: stake returned

        bet.status = _STATUS[outcome]
        bet.pnl = round(profit(outcome, bet.odds_taken, bet.stake), 2)
        bet.odds_closing, bet.clv = _closing(session, prediction, bet.odds_taken)
        bet.settled_at = now
        return SettledBet(
            bet_id=bet.id,
            run_date=prediction.run_date,
            rank=prediction.rank,
            home=match.home_team.name,
            away=match.away_team.name,
            score=f"{match.home_goals}:{match.away_goals}" if match.home_goals is not None else None,
            market=prediction.market,
            selection=prediction.selection,
            line=prediction.line,
            odds=bet.odds_taken,
            status=bet.status,
            pnl=bet.pnl,
            clv=bet.clv,
        )

    def _settle_stage(self) -> StageStatus | None:
        with self._db.session() as session:
            stage = session.scalar(
                select(PyramidStage).where(PyramidStage.status == StageStatus.PENDING).order_by(PyramidStage.id.desc()).limit(1)
            )
            if stage is None or stage.bet_id is None:
                return None
            bet = session.get(Bet, stage.bet_id)
            if bet is None or bet.status == BetStatus.PENDING:
                return None
            outcome = _STAGE.get(BetStatus(bet.status), StageStatus.VOID)
        self._pyramid.settle_stage(outcome)
        return outcome

    def open_stage(self, day: date, now: datetime | None = None) -> bool:
        """Paper mode: the day's top pick becomes the next pyramid stage (if none is pending)."""
        if not self._paper_mode:
            return False
        now = now or utcnow()
        state = self._pyramid.get_state()
        if state.pending_odds is not None or state.target_reached:
            return False
        with self._db.session() as session:
            top = session.scalar(
                select(Prediction)
                .options(joinedload(Prediction.match))
                .where(Prediction.run_date == day, Prediction.rank == 1)
                .order_by(Prediction.id.desc())
                .limit(1)
            )
            if top is None or top.match.kickoff_utc <= now:
                return False
            line_filter = Prediction.line.is_(None) if top.line is None else Prediction.line == top.line
            bet = session.scalar(
                select(Bet)
                .join(Prediction, Prediction.id == Bet.prediction_id)
                .where(
                    Bet.is_paper.is_(True),
                    Bet.status == BetStatus.PENDING,
                    Prediction.match_id == top.match_id,
                    Prediction.market == top.market,
                    Prediction.selection == top.selection,
                    line_filter,
                )
                .limit(1)
            )
            if bet is None:
                return False
            already = session.scalar(select(PyramidStage.id).where(PyramidStage.bet_id == bet.id))
            if already is not None:
                return False
            odds, bet_id = bet.odds_taken, bet.id
        self._pyramid.open_stage(odds, bet_id=bet_id)
        return True


# ------------------------------------------------------------------ statistics


@dataclass(frozen=True)
class TrackRecord:
    bets: int
    wins: int
    losses: int
    voids: int
    pending: int
    staked: float
    pnl: float
    avg_odds: float | None
    avg_clv: float | None

    @property
    def hit_rate(self) -> float | None:
        decided = self.wins + self.losses
        return self.wins / decided if decided else None

    @property
    def roi(self) -> float | None:
        return self.pnl / self.staked if self.staked else None


def track_record(db: Database, *, since: date | None = None, top_only: bool = False) -> TrackRecord:
    """The paper bets' results (all picks, or only the days' top picks)."""
    with db.session() as session:
        stmt = select(Bet, Prediction).join(Prediction, Prediction.id == Bet.prediction_id).where(Bet.is_paper.is_(True))
        if since is not None:
            stmt = stmt.where(Prediction.run_date >= since)
        if top_only:
            stmt = stmt.where(Prediction.rank == 1)
        rows = session.execute(stmt).all()
        settled = [b for b, _ in rows if b.status != BetStatus.PENDING]
        decided = [b for b in settled if b.status in (BetStatus.WON, BetStatus.LOST)]
        clvs = [b.clv for b in settled if b.clv is not None]
        return TrackRecord(
            bets=len(settled),
            wins=sum(1 for b in settled if b.status == BetStatus.WON),
            losses=sum(1 for b in settled if b.status == BetStatus.LOST),
            voids=sum(1 for b in settled if b.status == BetStatus.VOID),
            pending=len(rows) - len(settled),
            staked=sum(b.stake for b in decided),
            pnl=round(sum(b.pnl or 0 for b in settled), 2),
            avg_odds=sum(b.odds_taken for b in decided) / len(decided) if decided else None,
            avg_clv=sum(clvs) / len(clvs) if clvs else None,
        )
